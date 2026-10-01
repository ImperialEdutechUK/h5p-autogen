import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, Error as PlaywrightError

CREATE_URL = "https://imperiallearning.h5p.com/content/create"
OUT_FILE = "h5p_storage_state.min.json"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # H5P.com may immediately redirect /content/create to /login.
        # That redirect can interrupt page.goto(), so treat that as expected.
        try:
            await page.goto(CREATE_URL, wait_until="commit", timeout=60000)
        except PlaywrightError as exc:
            if "interrupted by another navigation" not in str(exc).lower():
                print(f"Navigation warning: {exc}")

        await page.wait_for_timeout(2000)

        print("")
        print("A browser window has opened.")
        print("1. Log in to H5P.com manually, including SSO/MFA if required.")
        print("2. After login, go to:")
        print(f"   {CREATE_URL}")
        print("3. Make sure you can see the 'Create New Content' page.")
        print("4. Return to this terminal and press Enter.")
        print("")

        # Keep Playwright's event loop alive while waiting for the user.
        await asyncio.to_thread(
            input,
            "Press Enter only after the Create New Content page is visible... "
        )

        # Verify we are no longer on the login page.
        current_url = page.url
        if "/login" in current_url.lower():
            print("")
            print("You are still on the H5P login page.")
            print("Complete the login in the browser, open the Create New Content page,")
            print("then return here and press Enter again.")
            await asyncio.to_thread(input, "Press Enter when ready... ")
            current_url = page.url

        if "imperiallearning.h5p.com" not in current_url:
            print(f"Warning: current browser URL is {current_url}")

        state = await context.storage_state()

        # Save a minified one-line JSON value for easy Railway paste.
        Path(OUT_FILE).write_text(
            json.dumps(state, separators=(",", ":")),
            encoding="utf-8",
        )

        print("")
        print(f"Saved authenticated H5P session to: {Path(OUT_FILE).resolve()}")
        print("")
        print("Next:")
        print("1. Open that file.")
        print("2. Copy the entire one-line JSON.")
        print("3. Add it to Railway as H5P_STORAGE_STATE_JSON.")
        print("4. Redeploy Railway.")
        print("")
        print("Do not upload this JSON file to GitHub. It contains login-session data.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
