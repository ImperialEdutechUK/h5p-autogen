import json
import os
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


class H5PAutomationError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


async def _first_visible(scope, selectors, timeout_ms: int = 2500):
    for selector in selectors:
        try:
            loc = scope.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except Exception:
            continue
    return None


async def _first_attached(scope, selectors, timeout_ms: int = 2500):
    for selector in selectors:
        try:
            loc = scope.locator(selector).first
            await loc.wait_for(state="attached", timeout=timeout_ms)
            return loc
        except Exception:
            continue
    return None


def _scopes(page):
    """Return the top-level page plus every currently attached frame.

    H5P Hub/editor controls can be rendered in an iframe. Searching only the
    top-level page misses the upload input on some H5P.com accounts.
    """
    scopes = [page]
    for frame in page.frames:
        if frame != page.main_frame:
            scopes.append(frame)
    return scopes


async def _click_role_or_text_in_scope(scope, names, timeout_ms: int = 2500) -> bool:
    for name in names:
        for role in ("button", "radio", "link", "tab"):
            try:
                loc = scope.get_by_role(role, name=name, exact=False).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                await loc.click()
                return True
            except Exception:
                pass
        try:
            loc = scope.get_by_text(name, exact=True).first
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.click()
            return True
        except Exception:
            pass
    return False


async def _click_role_or_text(page, names, timeout_ms: int = 2500) -> bool:
    for scope in _scopes(page):
        if await _click_role_or_text_in_scope(scope, names, timeout_ms=timeout_ms):
            return True
    return False


async def _load_storage_state() -> Optional[dict]:
    raw = _env("H5P_STORAGE_STATE_JSON")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception as exc:
        raise H5PAutomationError("H5P_STORAGE_STATE_JSON is not valid JSON.") from exc


async def _login_h5p(page) -> None:
    """Log in to H5P.com when the browser is not already authenticated."""
    create_url = _env("H5P_CREATE_URL", "https://imperiallearning.h5p.com/content/create")

    try:
        await page.goto(create_url, wait_until="domcontentloaded", timeout=60000)
    except Exception as exc:
        # H5P.com can interrupt the original navigation while redirecting to login.
        if "interrupted by another navigation" not in str(exc).lower():
            raise

    # If Create New Content is already visible, the session is authenticated.
    try:
        if await page.get_by_text("Create New Content", exact=False).first.is_visible(timeout=3500):
            return
    except Exception:
        pass

    username = _env("H5P_USERNAME")
    password = _env("H5P_PASSWORD")
    if not username or not password:
        raise H5PAutomationError(
            "H5P.com login is required. Set H5P_USERNAME and H5P_PASSWORD, "
            "or provide H5P_STORAGE_STATE_JSON for SSO/MFA accounts."
        )

    user = await _first_visible(page, [
        'input[type="email"]',
        'input[name="email"]',
        'input[name="username"]',
        'input[id*="email"]',
        'input[id*="user"]',
    ], timeout_ms=7000)
    pwd = await _first_visible(page, [
        'input[type="password"]',
        'input[name="password"]',
        'input[id*="password"]',
    ], timeout_ms=7000)

    if not user or not pwd:
        raise H5PAutomationError(
            "Could not find the H5P.com login fields. If your organisation uses SSO/MFA, "
            "use H5P_STORAGE_STATE_JSON."
        )

    await user.fill(username)
    await pwd.fill(password)
    clicked = await _click_role_or_text(page, ["Log in", "Login", "Sign in"], timeout_ms=3500)
    if not clicked:
        await pwd.press("Enter")

    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    await page.goto(create_url, wait_until="domcontentloaded")
    try:
        await page.get_by_text("Create New Content", exact=False).first.wait_for(state="visible", timeout=8000)
    except Exception as exc:
        raise H5PAutomationError("H5P.com login did not reach the Create New Content page.") from exc


async def _wait_for_h5p_hub(page, timeout_ms: int = 30000) -> None:
    """Wait for the H5P Hub create/upload widget to finish loading."""
    deadline_steps = max(1, timeout_ms // 500)
    for _ in range(deadline_steps):
        for scope in _scopes(page):
            try:
                # The user's H5P.com page shows Create Content / Upload inside the hub.
                upload = scope.get_by_text("Upload", exact=True).first
                if await upload.count() and await upload.is_visible():
                    return
            except Exception:
                pass
            try:
                if await scope.locator('input[type="radio"]').count():
                    txt = (await scope.locator("body").inner_text()).lower()
                    if "upload" in txt and "create content" in txt:
                        return
            except Exception:
                pass
        await page.wait_for_timeout(500)
    raise H5PAutomationError("H5P Hub did not finish loading the Create Content / Upload controls.")


async def _select_upload_mode(page) -> None:
    await _wait_for_h5p_hub(page)

    # Search both the top-level page and any iframe used by H5P Hub.
    for scope in _scopes(page):
        try:
            radio = scope.get_by_role("radio", name="Upload", exact=False).first
            await radio.wait_for(state="attached", timeout=1500)
            try:
                await radio.check(force=True)
            except Exception:
                await radio.click(force=True)
            await page.wait_for_timeout(700)
            return
        except Exception:
            pass

    # Common H5P Hub markup fallbacks.
    selectors = [
        'label:has-text("Upload")',
        'input[type="radio"][value*="upload" i]',
        'input[type="radio"][id*="upload" i]',
        '[class*="upload" i] input[type="radio"]',
        '[class*="h5p"] label:has-text("Upload")',
    ]
    for scope in _scopes(page):
        for sel in selectors:
            try:
                loc = scope.locator(sel).first
                await loc.wait_for(state="attached", timeout=1200)
                await loc.click(force=True)
                await page.wait_for_timeout(700)
                return
            except Exception:
                pass

    if await _click_role_or_text(page, ["Upload"], timeout_ms=2000):
        await page.wait_for_timeout(700)
        return

    raise H5PAutomationError("Could not switch H5P.com to Upload mode.")


async def _find_upload_input(page, timeout_ms: int = 30000):
    """Find the .h5p file input in the page OR an embedded H5P Hub frame.

    The input is commonly hidden, so this function deliberately looks for
    attached elements rather than requiring visibility.
    """
    selectors = [
        'input[type="file"][accept*=".h5p" i]',
        'input[type="file"][accept*="h5p" i]',
        'input[type="file"][name*="h5p" i]',
        'input[type="file"][id*="h5p" i]',
        'input[type="file"][class*="h5p" i]',
        'input[type="file"]',
    ]

    steps = max(1, timeout_ms // 400)
    for _ in range(steps):
        # Frames can appear after the H5P Hub loads, so rebuild scopes each loop.
        for scope in _scopes(page):
            for selector in selectors:
                try:
                    loc = scope.locator(selector)
                    count = await loc.count()
                    if count:
                        return loc.first
                except Exception:
                    pass
        await page.wait_for_timeout(400)
    return None


async def _upload_package(page, package: Path) -> None:
    file_input = await _find_upload_input(page, timeout_ms=30000)

    if not file_input:
        frame_info = []
        for frame in page.frames:
            try:
                count = await frame.locator('input[type="file"]').count()
            except Exception:
                count = -1
            frame_info.append(f"{frame.url or '<no-url>'} (file_inputs={count})")
        details = "; ".join(frame_info[:8]) or "no frames detected"
        raise H5PAutomationError(
            "Could not find the H5P.com .h5p upload field after switching to Upload mode. "
            f"Frames checked: {details}"
        )

    await file_input.set_input_files(str(package))

    # Wait for H5P.com to process the selected package. Some versions do this
    # automatically; others expose a separate Upload / Use / Import button.
    await page.wait_for_timeout(1200)

    # Only click an action button if one is visible. Search inside frames too.
    await _click_role_or_text(page, ["Use", "Import", "Continue", "Upload"], timeout_ms=1800)

    # Wait for the editor to replace the upload panel. Avoid networkidle because
    # H5P editor pages can keep background requests alive.
    await page.wait_for_timeout(2500)


async def _visible_control_texts(page, limit: int = 30) -> list[str]:
    """Return visible button/link control labels for diagnostics."""
    found = []
    selectors = [
        'button',
        '[role="button"]',
        'input[type="submit"]',
        'input[type="button"]',
        'a',
    ]
    for scope in _scopes(page):
        for selector in selectors:
            try:
                loc = scope.locator(selector)
                count = min(await loc.count(), 40)
                for i in range(count):
                    el = loc.nth(i)
                    try:
                        if not await el.is_visible():
                            continue
                        text = (await el.inner_text()).strip()
                    except Exception:
                        text = ""
                    if not text:
                        try:
                            text = (await el.get_attribute("value") or "").strip()
                        except Exception:
                            text = ""
                    if text and text not in found:
                        found.append(text)
                        if len(found) >= limit:
                            return found
            except Exception:
                continue
    return found


async def _find_save_control(page, timeout_ms: int = 90000):
    """Wait for H5P.com to finish importing and expose a Save control.

    Large .h5p packages can take considerably longer than a few seconds to
    unpack and initialise in the editor. The previous automation looked for
    Save almost immediately, which caused false failures.
    """
    selectors = [
        'button:text-is("Save")',
        'button:has-text("Save")',
        '[role="button"]:has-text("Save")',
        'input[type="submit"][value="Save"]',
        'input[type="button"][value="Save"]',
        'input[type="submit"][value*="save" i]',
        'input[type="button"][value*="save" i]',
        '[data-testid*="save" i]',
        '[id*="save" i]',
        '[class*="save" i]',
        'a:has-text("Save")',
    ]

    steps = max(1, timeout_ms // 1000)
    for step in range(steps):
        # H5P.com normally places Save in the outer shell, but search frames too.
        for scope in _scopes(page):
            try:
                loc = scope.get_by_role("button", name="Save", exact=True).first
                if await loc.count() and await loc.is_visible():
                    return loc
            except Exception:
                pass

            for selector in selectors:
                try:
                    loc = scope.locator(selector).first
                    if await loc.count() and await loc.is_visible():
                        return loc
                except Exception:
                    continue

        # Some H5P packages expose an Import/Use/Continue action only after
        # server-side validation has completed. Click it if it appears.
        if step in {2, 5, 10, 20, 35, 50}:
            try:
                await _click_role_or_text(page, ["Use", "Import", "Continue"], timeout_ms=900)
            except Exception:
                pass

        await page.wait_for_timeout(1000)

    return None


async def _save_content(page) -> str:
    save = await _find_save_control(page, timeout_ms=90000)
    if not save:
        controls = await _visible_control_texts(page)
        try:
            body = (await page.locator("body").inner_text())[:1400].replace("\n", " | ")
        except Exception:
            body = ""
        raise H5PAutomationError(
            "H5P.com Save button was not found after waiting for the imported editor to load. "
            f"Current URL: {page.url}. "
            f"Visible controls: {controls[:20]}. "
            f"Page text: {body[:900]}"
        )

    old_url = page.url
    try:
        await save.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        await save.click(timeout=10000)
    except Exception:
        await save.click(force=True, timeout=10000)

    # Saving normally navigates from /content/create to /content/<id>.
    try:
        await page.wait_for_url(lambda url: str(url) != old_url, timeout=30000)
    except Exception:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

    await page.wait_for_timeout(1500)
    return page.url


async def _inspect_test_target(page, target_url: str) -> dict:
    """Visit the configured testing URL and report what is visible."""
    if not target_url:
        return {"configured": False}
    original = page.url
    try:
        await page.goto(target_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(800)
        body_text = (await page.locator("body").inner_text())[:8000]
        title = await page.title()
        lower = body_text.lower()
        kind = "unknown"
        if "create new content" in lower:
            kind = "create"
        elif "edit content" in lower or ("save" in lower and "content" in lower):
            kind = "content"
        elif "folder" in lower or "move to" in lower:
            kind = "folder_or_collection"
        return {
            "configured": True,
            "url": page.url,
            "title": title,
            "detected_kind": kind,
        }
    finally:
        try:
            await page.goto(original, wait_until="domcontentloaded")
        except Exception:
            pass


async def automate_h5p_com_import(
    h5p_path: str,
    *,
    auto_save: bool = True,
    inspect_test_target: bool = True,
) -> dict:
    """Import a generated .h5p package into Imperial Learning's H5P.com account.

    Flow: authenticate -> /content/create -> Upload -> choose .h5p -> Save -> return URL.
    """
    package = Path(h5p_path)
    if not package.exists():
        raise H5PAutomationError(f"H5P package not found: {package}")

    create_url = _env("H5P_CREATE_URL", "https://imperiallearning.h5p.com/content/create")
    test_target_url = _env(
        "H5P_TEST_TARGET_URL",
        "https://imperiallearning.h5p.com/content/1292811530103001337",
    )
    headless = _env("H5P_BROWSER_HEADLESS", "1").lower() not in {"0", "false", "no"}
    storage_state = await _load_storage_state()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context_kwargs = {"ignore_https_errors": True}
        if storage_state:
            context_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        page.set_default_timeout(9000)

        try:
            await _login_h5p(page)

            target_info = {"configured": False}
            if inspect_test_target and test_target_url:
                target_info = await _inspect_test_target(page, test_target_url)

            await page.goto(create_url, wait_until="domcontentloaded")
            try:
                await page.get_by_text("Create New Content", exact=False).first.wait_for(
                    state="visible", timeout=7000
                )
            except Exception as exc:
                raise H5PAutomationError("H5P.com Create New Content page did not load.") from exc

            await _wait_for_h5p_hub(page)
            await _select_upload_mode(page)
            await _upload_package(page, package)

            final_url = page.url
            if auto_save:
                final_url = await _save_content(page)

            return {
                "ok": True,
                "url": final_url,
                "create_url": create_url,
                "test_target": target_info,
                "package_name": package.name,
            }
        finally:
            await context.close()
            await browser.close()


async def check_h5p_com_connection() -> dict:
    """Check authentication and inspect the configured testing URL without uploading."""
    create_url = _env("H5P_CREATE_URL", "https://imperiallearning.h5p.com/content/create")
    test_target_url = _env(
        "H5P_TEST_TARGET_URL",
        "https://imperiallearning.h5p.com/content/1292811530103001337",
    )
    headless = _env("H5P_BROWSER_HEADLESS", "1").lower() not in {"0", "false", "no"}
    storage_state = await _load_storage_state()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context_kwargs = {"ignore_https_errors": True}
        if storage_state:
            context_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        page.set_default_timeout(9000)
        try:
            await _login_h5p(page)
            target_info = await _inspect_test_target(page, test_target_url)
            return {
                "ok": True,
                "authenticated": True,
                "create_url": create_url,
                "test_target": target_info,
            }
        finally:
            await context.close()
            await browser.close()
