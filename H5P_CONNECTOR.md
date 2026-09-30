# H5P Receiver Contract

The Streamlit generator now supports sending a generated H5P package directly to an H5P host.

## Request

The configured `H5P_IMPORT_ENDPOINT` receives:

- Method: `POST`
- Content type: `multipart/form-data`
- File field: `file`
- File type: `.h5p`
- Optional header: `Authorization: Bearer <H5P_IMPORT_TOKEN>`

## Response

Return JSON with the URL of the newly imported activity's editor:

```json
{
  "edit_url": "https://your-site.example/h5p/edit/123"
}
```

The generator also accepts `editor_url` or `url`.

## What the receiver needs to do

1. Authenticate the request.
2. Validate the uploaded `.h5p` package.
3. Import the package into the target H5P installation.
4. Create/save the H5P content record.
5. Return the editor URL for that new content record.

Once the target platform is confirmed (Moodle, WordPress or H5P.com), implement this small receiver using that platform's supported APIs/plugin hooks.
