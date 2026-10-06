import json
import os
import zipfile
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


AUTOMATION_VERSION = "2026-10-06-visible-upload-use-v4"


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


async def _find_upload_input(page, timeout_ms: int = 15000):
    """Find the file input that belongs to the *visible H5P package uploader*.

    H5P.com can keep more than one hidden ``input[type=file]`` in the DOM. The
    old automation used the first file input it found, which could belong to a
    different widget. Railway logs then reported that the file was selected,
    but H5P Hub never started importing it.

    This version scores each file input by its own attributes and by text in
    nearby ancestors. It only returns an input that is clearly associated with
    the H5P Hub panel containing "Upload an H5P file" / "Upload a file".
    """
    steps = max(1, timeout_ms // 350)

    for _ in range(steps):
        best = None  # (score, scope, index, details)

        for scope in _scopes(page):
            try:
                inputs = scope.locator('input[type="file"]')
                count = await inputs.count()
            except Exception:
                continue

            for i in range(count):
                loc = inputs.nth(i)
                try:
                    info = await loc.evaluate(
                        """el => {
                          const lower = v => String(v || '').toLowerCase();
                          const accept = lower(el.getAttribute('accept'));
                          const name = lower(el.getAttribute('name'));
                          const id = lower(el.getAttribute('id'));
                          const cls = lower(el.getAttribute('class'));
                          let score = 0;
                          if (accept.includes('h5p') || accept.includes('.h5p')) score += 120;
                          if (name.includes('h5p')) score += 70;
                          if (id.includes('h5p')) score += 70;
                          if (cls.includes('h5p')) score += 35;

                          let context = '';
                          let p = el.parentElement;
                          for (let depth = 0; p && depth < 8; depth++, p = p.parentElement) {
                            if (p.tagName === 'BODY' || p.tagName === 'HTML') break;
                            const t = lower(p.innerText).replace(/\\s+/g, ' ').trim();
                            if (!t) continue;
                            if (!context || t.length < context.length) context = t;
                            if (t.includes('upload an h5p file')) score += 150;
                            if (t.includes('upload a file')) score += 100;
                            if (t.includes('no file chosen')) score += 40;
                            if (t.includes('create content') && t.includes('upload')) score += 15;
                          }

                          return {
                            score,
                            accept,
                            name,
                            id,
                            cls,
                            context: context.slice(0, 260)
                          };
                        }"""
                    )
                except Exception:
                    continue

                score = int((info or {}).get('score') or 0)
                if best is None or score > best[0]:
                    best = (score, scope, i, info or {})

        if best and best[0] >= 80:
            score, scope, index, info = best
            print(
                "[H5P] Matched H5P package input "
                f"(score={score}, accept={info.get('accept')!r}, "
                f"name={info.get('name')!r}, id={info.get('id')!r}, "
                f"context={info.get('context')!r}).",
                flush=True,
            )
            return scope.locator('input[type="file"]').nth(index)

        await page.wait_for_timeout(350)

    return None


async def _click_upload_file_and_choose(page, package: Path) -> bool:
    """Choose the package through H5P Hub's real Upload a file control.

    H5P Hub normally starts parsing/uploading only after the visible Upload a
    file control fires its file-chooser/change flow. Prefer that path. A hidden
    contextual input is retained only as a fallback for tenant variants that do
    not emit a file-chooser event in headless Chromium.
    """
    button_selectors = [
        'button:has-text("Upload a file")',
        '[role="button"]:has-text("Upload a file")',
        'label:has-text("Upload a file")',
        'a:has-text("Upload a file")',
        'text="Upload a file"',
    ]

    # Preferred path: activate the same visible control a user clicks.
    for scope in _scopes(page):
        for selector in button_selectors:
            try:
                locs = scope.locator(selector)
                count = await locs.count()
            except Exception:
                continue

            for i in range(count):
                loc = locs.nth(i)
                try:
                    if not await loc.is_visible():
                        continue
                except Exception:
                    continue

                try:
                    async with page.expect_file_chooser(timeout=5000) as chooser_info:
                        await loc.click(timeout=4000, force=True)
                    chooser = await chooser_info.value
                    await chooser.set_files(str(package))
                    print(
                        f"[H5P] Selected package through visible Upload a file control: {package.name}",
                        flush=True,
                    )
                    return True
                except Exception:
                    # Some Hub builds create/activate a hidden file input without
                    # raising Playwright's filechooser event. If so, use the
                    # contextual H5P input immediately after the visible click.
                    contextual_input = await _find_upload_input(page, timeout_ms=2500)
                    if contextual_input is not None:
                        try:
                            await contextual_input.set_input_files(str(package))
                            print(
                                f"[H5P] Selected package after activating Upload a file: {package.name}",
                                flush=True,
                            )
                            return True
                        except Exception:
                            pass

    # Last-resort path for tenants where the styled control is inaccessible in
    # headless mode but the correct H5P file input is present.
    contextual_input = await _find_upload_input(page, timeout_ms=6000)
    if contextual_input is not None:
        try:
            await contextual_input.set_input_files(str(package))
            print(
                f"[H5P] Selected package through contextual H5P upload input fallback: {package.name}",
                flush=True,
            )
            return True
        except Exception as exc:
            print(f"[H5P] Contextual upload input fallback failed: {exc}", flush=True)

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




def _package_title(package: Path) -> str:
    """Read the generated H5P title so we can detect when H5P.com has really loaded it."""
    try:
        with zipfile.ZipFile(package, "r") as zf:
            data = json.loads(zf.read("h5p.json").decode("utf-8"))
        return str(data.get("title") or "").strip()
    except Exception:
        return ""


async def _package_title_is_loaded(page, title: str) -> bool:
    """Detect the imported package by its title in text or editor form values."""
    title = " ".join((title or "").split()).strip().lower()
    if not title:
        return False

    for scope in _scopes(page):
        try:
            body = " ".join((await scope.locator("body").inner_text()).split()).lower()
            if title in body:
                return True
        except Exception:
            pass

        # H5P editor titles are commonly values of inputs and therefore do not
        # necessarily appear in innerText. Check form control values explicitly.
        for selector in ('input', 'textarea'):
            try:
                locs = scope.locator(selector)
                count = min(await locs.count(), 100)
                for i in range(count):
                    try:
                        value = " ".join((await locs.nth(i).input_value()).split()).lower()
                        if value and (title in value or value in title):
                            return True
                    except Exception:
                        pass
            except Exception:
                pass
    return False


async def _next_import_action(page):
    """Find a post-file-selection action H5P.com may require before the editor loads.

    H5P.com deployments differ: some auto-import after file selection, while
    others expose Upload, Import, Use or Continue. We support all of them.
    """
    # After a file has been chosen, H5P Hub uploads it automatically and then
    # exposes Use. Do not click the Upload tab again here: doing so can reset or
    # interrupt the selected package on some H5P.com tenants.
    names = ["Use", "Import", "Continue"]
    for scope in _scopes(page):
        for name in names:
            try:
                loc = scope.get_by_role("button", name=name, exact=True).first
                if await loc.count() and await loc.is_visible() and await loc.is_enabled():
                    return loc, name
            except Exception:
                pass
            for selector in (
                f'button:has-text("{name}")',
                f'input[type="submit"][value="{name}" i]',
                f'input[type="button"][value="{name}" i]',
                f'[role="button"]:has-text("{name}")',
            ):
                try:
                    locs = scope.locator(selector)
                    for i in range(await locs.count()):
                        loc = locs.nth(i)
                        if not await loc.is_visible() or not await loc.is_enabled():
                            continue
                        txt = ""
                        val = ""
                        try:
                            txt = (await loc.inner_text()).strip().lower()
                        except Exception:
                            pass
                        try:
                            val = (await loc.get_attribute("value") or "").strip().lower()
                        except Exception:
                            pass
                        if txt == name.lower() or val == name.lower():
                            return loc, name
                except Exception:
                    pass
    return None, ""

async def _visible_text(scope) -> str:
    try:
        return (await scope.locator("body").inner_text()).lower()
    except Exception:
        return ""


async def _visible_use_control(page):
    """Return H5P Hub's visible Use button after a package upload completes."""
    selectors = [
        'button:has-text("Use")',
        'a:has-text("Use")',
        'input[type="button"][value="Use" i]',
        'input[type="submit"][value="Use" i]',
        '[role="button"]:has-text("Use")',
        '.h5p-hub-button:has-text("Use")',
    ]
    for scope in _scopes(page):
        try:
            loc = scope.get_by_role("button", name="Use", exact=True).first
            if await loc.count() and await loc.is_visible() and await loc.is_enabled():
                return loc
        except Exception:
            pass
        for selector in selectors:
            try:
                locs = scope.locator(selector)
                for i in range(await locs.count()):
                    loc = locs.nth(i)
                    if await loc.is_visible() and await loc.is_enabled():
                        # Avoid matching unrelated text that merely contains "Use".
                        try:
                            txt = (await loc.inner_text()).strip().lower()
                            val = (await loc.get_attribute("value") or "").strip().lower()
                            if txt == "use" or val == "use":
                                return loc
                        except Exception:
                            return loc
            except Exception:
                pass
    return None


async def _hub_upload_error(page) -> str:
    """Return a visible H5P Hub upload/validation error, if one is present."""
    needles = [
        "could not be uploaded",
        "selected file could not be uploaded",
        "unable to interpret response",
        "invalid h5p",
        "invalid package",
        "validation failed",
        "only files with the .h5p extension",
        "file is too large",
        "upload failed",
        "error uploading",
        "subscription is past due",
        "your subscription is past due",
        "pay for your subscription",
        "subscription has expired",
    ]
    for scope in _scopes(page):
        text = await _visible_text(scope)
        if not text:
            continue
        for needle in needles:
            if needle in text:
                # Return a compact excerpt around the error phrase.
                pos = text.find(needle)
                a = max(0, pos - 180)
                b = min(len(text), pos + 500)
                return " ".join(text[a:b].split())
    return ""


async def _editor_is_ready(page) -> bool:
    """Strictly detect that H5P Hub has loaded the uploaded package into the editor.

    Important: selecting a file is not enough. H5P's normal reuse flow is
    Upload -> choose .h5p -> Use -> editor. The outer H5P.com Save button is
    visible even before that sequence completes, so we must not use it as an
    editor-ready signal.
    """
    # If H5P Hub still exposes a Use button, the package has uploaded but has not
    # yet been inserted into the editor.
    if await _visible_use_control(page):
        return False

    editor_selectors = [
        '.h5peditor-form',
        '.h5peditor-field',
        '.h5peditor-label',
        '.h5peditor-text',
        '.h5peditor-textarea',
        '.h5peditor-metadata',
        '[class*="h5peditor-form"]',
        '[class*="h5peditor-field"]',
    ]

    for scope in _scopes(page):
        body = await _visible_text(scope)
        if not body:
            continue

        # These are definitive signs that H5P Hub is still in upload/selection
        # mode rather than showing the imported editor.
        still_uploading = [
            "upload an h5p file",
            "select content type or upload content",
            "now uploading",
        ]
        if any(marker in body for marker in still_uploading):
            continue

        # Prefer real H5P editor DOM markers over text heuristics.
        for selector in editor_selectors:
            try:
                locs = scope.locator(selector)
                for i in range(min(await locs.count(), 8)):
                    if await locs.nth(i).is_visible():
                        return True
            except Exception:
                pass

        # Fallback: require multiple editor-specific labels, not merely the
        # changed content-type heading. This avoids the previous false positive
        # where Save was clicked while H5P Hub was still waiting for "Use".
        editor_labels = [
            "metadata",
            "task description",
            "behavioural settings",
            "behavioral settings",
            "overall feedback",
            "tutorial",
        ]
        label_hits = sum(1 for marker in editor_labels if marker in body)
        if label_hits >= 2:
            return True

        # A visible required Title field together with a non-Hub editor label is
        # also a strong signal for simpler content types.
        if ("title *" in body or "title*" in body) and any(
            marker in body for marker in ["media", "text", "question", "description"]
        ):
            return True

    return False


async def _wait_for_use_then_editor(page, package: Path, timeout_ms: int = 180000) -> None:
    """Wait for H5P.com to import the selected package and open its editor.

    Standard H5P Hub flow is Upload -> choose .h5p -> automatic upload -> Use
    -> editor. Some tenants skip the Use step. This loop handles both flows and
    emits progress messages so Railway does not appear to stall silently.
    """
    steps = max(1, timeout_ms // 500)
    title = _package_title(package)
    clicked_actions = set()
    success_logged = False

    for step in range(steps):
        err = await _hub_upload_error(page)
        if err:
            raise H5PAutomationError(f"H5P.com rejected the uploaded .h5p package: {err}")

        # H5P Hub's normal reuse flow shows Use only after the upload has
        # completed successfully. Click it as soon as it appears.
        use = await _visible_use_control(page)
        if use is not None and "use" not in clicked_actions:
            print("[H5P] Package upload completed. Clicking Use...", flush=True)
            try:
                await use.click(force=True)
                clicked_actions.add("use")
                await page.wait_for_timeout(1200)
                continue
            except Exception as exc:
                raise H5PAutomationError(
                    f"H5P.com showed Use after upload, but automation could not click it: {exc}"
                ) from exc

        if await _editor_is_ready(page):
            print("[H5P] Imported package is loaded in the editor.", flush=True)
            return

        if title and await _package_title_is_loaded(page, title):
            print(f"[H5P] Imported package title detected in editor: {title}", flush=True)
            return

        # Log successful upload text when present, even if Use has not appeared
        # yet. This distinguishes server-side H5P parsing from file-selection
        # problems in Railway logs.
        if not success_logged:
            for scope in _scopes(page):
                body = await _visible_text(scope)
                if "successfully uploaded" in body:
                    print("[H5P] H5P Hub reports that the package was successfully uploaded.", flush=True)
                    success_logged = True
                    break

        action, label = await _next_import_action(page)
        if action is not None:
            key = label.lower()
            if key not in clicked_actions:
                print(f"[H5P] Clicking {label} to advance package import...", flush=True)
                try:
                    await action.click(force=True)
                    clicked_actions.add(key)
                    await page.wait_for_timeout(1200)
                    continue
                except Exception as exc:
                    raise H5PAutomationError(
                        f"H5P.com showed a {label} action, but automation could not click it: {exc}"
                    ) from exc

        if step and step % 20 == 0:
            elapsed = (step * 500) // 1000
            print(f"[H5P] Still waiting for H5P Hub import/editor ({elapsed}s)...", flush=True)

        await page.wait_for_timeout(500)

    excerpt = ""
    try:
        excerpt = " ".join((await page.locator("body").inner_text()).split())[:1500]
    except Exception:
        pass
    buttons = []
    try:
        buttons = [x.strip() for x in await page.locator("button").all_inner_texts() if x.strip()][:30]
    except Exception:
        pass
    raise H5PAutomationError(
        "The .h5p file was selected, but H5P.com did not expose the imported activity editor. "
        f"Expected title: {title or '<unknown>'}. Visible buttons: {buttons}. Page excerpt: {excerpt}"
    )


async def _wait_for_import_to_finish(page, package: Path, timeout_ms: int = 180000) -> None:
    """Compatibility wrapper for the H5P.com import sequence."""
    await _wait_for_use_then_editor(page, package, timeout_ms=timeout_ms)


async def _upload_package(page, package: Path) -> None:
    print("[H5P] Upload mode selected. Choosing generated package...", flush=True)

    # Prefer the exact visible control in the Imperial Learning UI:
    # 'Upload a file'. This handles lazily-created/hidden inputs correctly.
    selected = await _click_upload_file_and_choose(page, package)

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
            "Could not identify or activate the H5P.com package upload control. "
            f"Frames checked: {details}"
        )

    await _wait_for_package_selected(page, package, timeout_ms=15000)
    print("[H5P] Generated .h5p file selected successfully.", flush=True)

    # H5P.com may auto-import immediately after selection or may expose an
    # Upload/Import/Use/Continue step. Wait for the real editor, not for any
    # single tenant-specific button.
    await _wait_for_use_then_editor(page, package, timeout_ms=180000)


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


async def _save_content(page, package: Path) -> str:
    # Import must already be complete before Save is clicked. The Save button is
    # visible even on an empty H5P create form, so clicking too early only triggers
    # 'Select content type or upload content.' validation.
    if not await _editor_is_ready(page):
        await _wait_for_import_to_finish(page, package, timeout_ms=180000)

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
    print(f"[H5P] Automation version: {AUTOMATION_VERSION}", flush=True)
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
                final_url = await _save_content(page, package)

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
