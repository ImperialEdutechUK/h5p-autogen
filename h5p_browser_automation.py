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


async def _find_upload_input(page, timeout_ms: int = 12000):
    """Find any attached file input used by the H5P Hub upload control."""
    selectors = [
        'input[type="file"][accept*=".h5p" i]',
        'input[type="file"][accept*="h5p" i]',
        'input[type="file"][name*="h5p" i]',
        'input[type="file"][id*="h5p" i]',
        'input[type="file"]',
    ]

    steps = max(1, timeout_ms // 300)
    for _ in range(steps):
        for scope in _scopes(page):
            for selector in selectors:
                try:
                    loc = scope.locator(selector)
                    if await loc.count():
                        return loc.first
                except Exception:
                    pass
        await page.wait_for_timeout(300)
    return None


async def _click_upload_file_and_choose(page, package: Path) -> bool:
    """Use the visible H5P Hub 'Upload a file' button and Playwright's file chooser.

    The Imperial Learning H5P.com UI shown by the user exposes a visible
    'Upload a file' button. On this UI the underlying <input type=file> can be
    hidden or created lazily, so setting an arbitrary hidden input is not
    reliable. Listening for the browser file chooser is the most robust path.
    """
    button_names = ["Upload a file", "Choose file", "Choose a file", "Browse"]

    for scope in _scopes(page):
        for name in button_names:
            candidates = []
            try:
                candidates.append(scope.get_by_role("button", name=name, exact=False).first)
            except Exception:
                pass
            try:
                candidates.append(scope.get_by_text(name, exact=True).first)
            except Exception:
                pass

            for loc in candidates:
                try:
                    await loc.wait_for(state="visible", timeout=1800)
                except Exception:
                    continue
                try:
                    async with page.expect_file_chooser(timeout=6000) as chooser_info:
                        await loc.click(force=True)
                    chooser = await chooser_info.value
                    await chooser.set_files(str(package))
                    print(f"[H5P] Selected package through file chooser: {package.name}", flush=True)
                    return True
                except Exception:
                    # Some H5P Hub builds wire the button to an already-existing
                    # hidden input instead of emitting a filechooser event.
                    continue
    return False


async def _package_is_selected(page, package: Path) -> bool:
    """Confirm that H5P Hub has actually received the selected file."""
    expected = package.name.lower()

    for scope in _scopes(page):
        # Check native input FileList first.
        try:
            inputs = scope.locator('input[type="file"]')
            for i in range(await inputs.count()):
                loc = inputs.nth(i)
                try:
                    names = await loc.evaluate(
                        "el => Array.from(el.files || []).map(f => f.name.toLowerCase())"
                    )
                    if expected in names:
                        return True
                except Exception:
                    pass
        except Exception:
            pass

        # H5P Hub also renders the chosen filename as text in many builds.
        try:
            body = (await scope.locator("body").inner_text()).lower()
            if expected in body:
                return True
        except Exception:
            pass

    return False


async def _wait_for_package_selected(page, package: Path, timeout_ms: int = 15000) -> None:
    steps = max(1, timeout_ms // 350)
    for _ in range(steps):
        if await _package_is_selected(page, package):
            return
        await page.wait_for_timeout(350)
    raise H5PAutomationError(
        "H5P.com opened Upload mode, but the generated .h5p file was not selected."
    )


async def _editor_is_ready(page) -> bool:
    """Detect when H5P has finished importing and the content editor is usable."""
    for scope in _scopes(page):
        try:
            body = (await scope.locator("body").inner_text()).lower()
        except Exception:
            continue

        # The validation message shown in the user's screenshot means import has
        # NOT completed yet.
        if "select content type or upload content" in body:
            continue

        # Common editor labels that appear after a content type/package loads.
        ready_markers = [
            "metadata",
            "task description",
            "behavioural settings",
            "behavioral settings",
            "overall feedback",
            "tutorial",
            "title *",
            "title*",
        ]
        if any(marker in body for marker in ready_markers):
            return True

        # The H5P Hub header starts as 'Select content type'. Once a package is
        # imported it normally changes to the actual activity type.
        if "create or upload content" in body and "select content type" not in body:
            return True

    return False


async def _wait_for_import_to_finish(page, timeout_ms: int = 120000) -> None:
    """Wait until the uploaded package has been parsed into the H5P editor."""
    steps = max(1, timeout_ms // 500)
    for step in range(steps):
        if await _editor_is_ready(page):
            print("[H5P] Imported package is loaded in the editor.", flush=True)
            return

        # Some H5P Hub versions show a second explicit Import/Use/Continue action
        # after the file is chosen. Do not click generic 'Upload' here because on
        # the Imperial UI that is the radio tab, not an import confirmation.
        if step % 4 == 0:
            await _click_role_or_text(
                page,
                ["Import", "Use", "Continue", "Insert"],
                timeout_ms=900,
            )
        await page.wait_for_timeout(500)

    # Include a short body excerpt to make future selector issues diagnosable.
    excerpt = ""
    try:
        excerpt = (await page.locator("body").inner_text()).replace("\n", " ")[:900]
    except Exception:
        pass
    raise H5PAutomationError(
        "The .h5p file was selected, but H5P.com did not finish importing it into the editor "
        f"within 120 seconds. Page excerpt: {excerpt}"
    )


async def _upload_package(page, package: Path) -> None:
    print("[H5P] Upload mode selected. Choosing generated package...", flush=True)

    # Prefer the exact visible control in the Imperial Learning UI:
    # 'Upload a file'. This handles lazily-created/hidden inputs correctly.
    selected = await _click_upload_file_and_choose(page, package)

    if not selected:
        # Fallback for H5P builds exposing a stable hidden input.
        file_input = await _find_upload_input(page, timeout_ms=12000)
        if file_input:
            try:
                await file_input.set_input_files(str(package))
                selected = True
                print(f"[H5P] Selected package through hidden input: {package.name}", flush=True)
            except Exception:
                selected = False

    if not selected:
        frame_info = []
        for frame in page.frames:
            try:
                count = await frame.locator('input[type="file"]').count()
            except Exception:
                count = -1
            frame_info.append(f"{frame.url or '<no-url>'} (file_inputs={count})")
        details = "; ".join(frame_info[:8]) or "no frames detected"
        raise H5PAutomationError(
            "Could not activate the H5P.com 'Upload a file' control. "
            f"Frames checked: {details}"
        )

    await _wait_for_package_selected(page, package, timeout_ms=15000)
    print("[H5P] Generated .h5p file selected successfully.", flush=True)
    await _wait_for_import_to_finish(page, timeout_ms=120000)


async def _find_save_control(page, timeout_ms: int = 30000):
    """Find H5P.com's outer-shell Save control after the editor is ready."""
    selectors = [
        'button:has-text("Save")',
        'input[type="submit"][value="Save" i]',
        'input[type="button"][value="Save" i]',
        'a:has-text("Save")',
        '[role="button"]:has-text("Save")',
        '[class*="save" i]',
    ]

    steps = max(1, timeout_ms // 400)
    for _ in range(steps):
        # Top-level page first: the user's screenshot shows Save in H5P.com's
        # outer header, not inside the Hub frame.
        scopes = [page] + [s for s in _scopes(page) if s is not page]
        for scope in scopes:
            for selector in selectors:
                try:
                    locs = scope.locator(selector)
                    count = await locs.count()
                    for i in range(count):
                        loc = locs.nth(i)
                        if not await loc.is_visible():
                            continue
                        disabled = await loc.get_attribute("disabled")
                        aria_disabled = await loc.get_attribute("aria-disabled")
                        if disabled is not None or (aria_disabled or "").lower() == "true":
                            continue
                        return loc
                except Exception:
                    pass

            # Accessible-name fallback.
            try:
                loc = scope.get_by_role("button", name="Save", exact=True).first
                if await loc.is_visible() and await loc.is_enabled():
                    return loc
            except Exception:
                pass
        await page.wait_for_timeout(400)
    return None


async def _save_content(page) -> str:
    # Import must already be complete before Save is clicked. The Save button is
    # visible even on an empty H5P create form, so clicking too early only triggers
    # 'Select content type or upload content.' validation.
    if not await _editor_is_ready(page):
        await _wait_for_import_to_finish(page, timeout_ms=120000)

    save = await _find_save_control(page, timeout_ms=30000)
    if not save:
        buttons = []
        try:
            texts = await page.locator("button").all_inner_texts()
            buttons = [x.strip() for x in texts if x.strip()][:20]
        except Exception:
            pass
        raise H5PAutomationError(
            "The H5P activity imported, but the H5P.com Save control could not be located. "
            f"Visible top-level buttons: {buttons}"
        )

    print("[H5P] Clicking Save...", flush=True)
    old_url = page.url
    try:
        await save.click(force=True)
    except Exception as exc:
        raise H5PAutomationError(f"H5P.com Save button was found but could not be clicked: {exc}") from exc

    # Wait for H5P.com to leave /content/create and return the new content URL.
    steps = 180  # up to 90 seconds
    for _ in range(steps):
        current = page.url
        if current != old_url and "/content/create" not in current:
            print(f"[H5P] Saved activity: {current}", flush=True)
            return current

        # Some H5P.com saves update history/state without a full navigation.
        if "/content/" in current and not current.rstrip("/").endswith("/content/create"):
            print(f"[H5P] Saved activity: {current}", flush=True)
            return current

        # Detect validation/errors instead of waiting forever.
        try:
            body = (await page.locator("body").inner_text()).lower()
            if "select content type or upload content" in body:
                raise H5PAutomationError(
                    "H5P.com rejected Save because the uploaded package had not been imported into the editor."
                )
        except H5PAutomationError:
            raise
        except Exception:
            pass
        await page.wait_for_timeout(500)

    # If no redirect occurred, return the current URL only if it already looks
    # like a saved content URL; otherwise treat it as a failed save.
    current = page.url
    if "/content/" in current and "/content/create" not in current:
        return current
    raise H5PAutomationError(
        "H5P.com Save was clicked, but no saved content URL appeared within 90 seconds."
    )


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
