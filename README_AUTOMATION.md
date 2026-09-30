# Imperial Learning H5P.com automation

This version skips Moodle completely.

## Automated flow
1. Your existing tool generates the `.h5p` package.
2. The frontend calls `POST /api/jobs/{job_id}/publish-to-h5p`.
3. Railway launches Chromium with Playwright.
4. It signs in to `imperiallearning.h5p.com`.
5. It opens `https://imperiallearning.h5p.com/content/create`.
6. It switches from **Create Content** to **Upload**.
7. It uploads the generated `.h5p` package.
8. H5P.com loads the correct editor for that activity type automatically.
9. It clicks **Save**.
10. The API returns the final H5P.com URL, which the frontend can open.

This avoids activity-specific copy/paste automation. Drag the Words, Essay, Quiz, Interactive Book, etc. can all use the same `.h5p` upload workflow.

## Testing URL
The configured testing URL is:

`https://imperiallearning.h5p.com/content/1292811530103001337`

The automation **inspects this URL first but does not automatically treat it as a folder**. A URL in the form `/content/<id>` may be a content item. This safety check prevents accidental replacement of existing H5P content. The `/api/h5p/automation-check` endpoint reports what the browser detects.

## Files
Replace/add these files in the Railway backend:

- `api.py` — replace
- `h5p_browser_automation.py` — add
- `generator_core.py` — use the OpenRouter-compatible version
- `Dockerfile` — replace if Railway is using Docker
- `requirements.browser.txt` — add

If your normal `requirements.txt` does not include Playwright, keep the supplied Dockerfile because it installs both requirements files and Chromium.

## Railway variables
Copy the values from `RAILWAY_VARIABLES.txt` into Railway.

For an H5P.com account with a normal username/password login:

```text
H5P_CREATE_URL=https://imperiallearning.h5p.com/content/create
H5P_TEST_TARGET_URL=https://imperiallearning.h5p.com/content/1292811530103001337
H5P_USERNAME=...
H5P_PASSWORD=...
H5P_BROWSER_HEADLESS=1
```

If your H5P.com login uses Microsoft/Google/SSO/MFA, do not put the identity-provider password into browser automation. Use `H5P_STORAGE_STATE_JSON` instead.

## First test
After Railway redeploys, open:

`GET /api/h5p/automation-check`

Expected result:

```json
{
  "ok": true,
  "authenticated": true,
  "create_url": "https://imperiallearning.h5p.com/content/create",
  "test_target": {
    "configured": true,
    "url": "...",
    "title": "...",
    "detected_kind": "content"
  }
}
```

Then generate an H5P normally and call:

`POST /api/jobs/{job_id}/publish-to-h5p`

with:

```json
{
  "auto_save": true,
  "inspect_test_target": true
}
```

## Frontend button
Rename the old **Send to H5P** button to **Publish to H5P** and call `/publish-to-h5p`. Use `FRONTEND_CALL_EXAMPLE.js` as the handler logic.
