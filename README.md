🌐 Live Demo :
https://h5p-autogen.streamlit.app/

## Open generated activity directly in H5P

The app can now send the generated `.h5p` package to an H5P host and open the returned editor URL.

Configure these environment variables or Streamlit secrets:

- `H5P_IMPORT_ENDPOINT` - an endpoint on your H5P host that accepts a multipart POST with a file field named `file`.
- `H5P_IMPORT_TOKEN` - optional Bearer token for the import endpoint.
- `H5P_EDITOR_URL` - optional fallback editor URL.

The import endpoint should return JSON in one of these forms:

```json
{"edit_url": "https://your-h5p-site.example/editor/123"}
```

or use `editor_url` / `url` instead of `edit_url`.

Flow:

1. Generate an activity in the web tool.
2. Click **Send to H5P**.
3. The web tool uploads the generated `.h5p` package to the configured endpoint.
4. Click **Open H5P Editor**.
5. The H5P editor opens with that generated activity already imported and ready to edit.

A real H5P host must provide the import endpoint. H5P itself does not expose one universal cross-platform import URL, so the exact receiver depends on whether the target is Moodle, WordPress, H5P.com, or another H5P integration.

## H5P editor integration

After generating an activity, the app now shows **Send to H5P** and **Open H5P Editor**.

Configure these values as environment variables or Streamlit secrets:

- `H5P_IMPORT_ENDPOINT` - receiver URL on your H5P/Moodle/WordPress server.
- `H5P_IMPORT_TOKEN` - optional Bearer token used by that receiver.
- `H5P_EDITOR_URL` - optional fallback editor URL.

The receiver must accept a multipart POST containing a field named `file` with the generated `.h5p` package. It must import/create the activity and return JSON containing its edit URL, for example:

```json
{
  "edit_url": "https://example.org/h5p/edit/123"
}
```

`editor_url` or `url` are also accepted.

This gives the following flow:

Web Tool -> AI generation -> H5P package -> Send to H5P -> Import -> Open H5P editor -> Review/Edit -> Save

The exact receiver implementation depends on the H5P host. Moodle, WordPress and H5P.com do not share one universal import API.
