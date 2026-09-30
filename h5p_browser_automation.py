import json
import os
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


class H5PAutomationError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


async def _first_visible(page, selectors, timeout_ms: int = 2500):
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except Exception:
            continue
    return None


async def _click_role_or_text(page, names, timeout_ms: int = 2500) -> bool:
    for name in names:
        for role in ("button", "radio", "link"):
            try:
                loc = page.get_by_role(role, name=name, exact=False).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                await loc.click()
                return True
            except Exception:
                pass
        try:
            loc = page.get_by_text(name, exact=True).first
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.click()
            return True
        except Exception:
            pass
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
    """Log in to H5P.com when the browser is not already authenticated.

    Supports normal email/username + password login. For SSO/MFA accounts use
    H5P_STORAGE_STATE_JSON instead so Railway starts with an authenticated session.
    """
    create_url = _env("H5P_CREATE_URL", "https://imperiallearning.h5p.com/content/create")
    await page.goto(create_url, wait_until="domcontentloaded")

    # If Create New Content is already visible, the session is authenticated.
    try:
        if await page.get_by_text("Create New Content", exact=False).first.is_visible(timeout=2500):
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

    # H5P.com may redirect to a dedicated login page.
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


async def _select_upload_mode(page) -> None:
    # The H5P.com create page shown by the user has Create Content / Upload radio choices.
    try:
        radio = page.get_by_role("radio", name="Upload", exact=False).first
        await radio.wait_for(state="attached", timeout=5000)
        await radio.check(force=True)
        return
    except Exception:
        pass

    # Fallback to visible label/text.
    if await _click_role_or_text(page, ["Upload"], timeout_ms=3500):
        return

    # JS/label fallback used by some H5P Hub builds.
    candidates = [
        'label:has-text("Upload")',
        'input[type="radio"][value*="upload" i]',
        '[class*="upload"] input[type="radio"]',
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="attached", timeout=2000)
            await loc.click(force=True)
            return
        except Exception:
            pass
    raise H5PAutomationError("Could not switch H5P.com to Upload mode.")


async def _upload_package(page, package: Path) -> None:
    file_input = None
    for _ in range(10):
        file_input = await _first_visible(page, [
            'input[type="file"][accept*="h5p" i]',
            'input[type="file"][name*="h5p" i]',
            'input[type="file"]',
        ], timeout_ms=700)
        if file_input:
            break
        await page.wait_for_timeout(400)

    if not file_input:
        # Hidden file inputs are common, so search attached elements as a final fallback.
        try:
            attached = page.locator('input[type="file"]').first
            await attached.wait_for(state="attached", timeout=3000)
            file_input = attached
        except Exception:
            pass

    if not file_input:
        raise H5PAutomationError("Could not find the H5P.com .h5p upload field.")

    await file_input.set_input_files(str(package))

    # H5P.com may automatically import after file selection or expose an Upload/Use button.
    await _click_role_or_text(page, ["Upload", "Use", "Import", "Continue"], timeout_ms=1800)
    await page.wait_for_timeout(1800)


async def _save_content(page) -> str:
    # Top-right H5P.com button in the user's screenshot.
    saved = await _click_role_or_text(page, ["Save"], timeout_ms=6000)
    if not saved:
        raise H5PAutomationError("H5P.com Save button was not found after import.")

    try:
        await page.wait_for_load_state("networkidle", timeout=18000)
    except Exception:
        pass
    await page.wait_for_timeout(1200)
    return page.url


async def _inspect_test_target(page, target_url: str) -> dict:
    """Visit the user's testing URL and return what the automation can observe.

    This does not assume that /content/<id> is a folder. It records the page title and
    whether folder/content-edit controls are visible so we do not accidentally move or
    overwrite content based on an incorrect assumption.
    """
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
        elif "edit content" in lower or "save" in lower and "content" in lower:
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
    The configured H5P_TEST_TARGET_URL is inspected but is NOT treated as a folder unless
    the page itself proves that it is one. This protects existing H5P content from being
    overwritten accidentally during testing.
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
