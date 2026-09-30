# H5P Activity Generator — FastAPI + Next.js

This version contains **no Streamlit**.

## Architecture

- **Railway:** FastAPI backend, PDF extraction, OpenAI calls, H5P package generation, H5P import bridge.
- **Vercel:** Next.js frontend.
- The frontend talks directly to the Railway API over HTTPS. There is no iframe and no Streamlit session/XSRF upload flow.

## Railway deployment

Deploy the repository root to Railway. Railway uses the root `Dockerfile`.

Required variable:

```text
LLM_API_KEY=...
```

Recommended after the Vercel domain is known:

```text
FRONTEND_ORIGINS=https://your-project.vercel.app
```

During the first test you may temporarily use:

```text
FRONTEND_ORIGINS=*
```

Optional H5P editor bridge:

```text
H5P_IMPORT_ENDPOINT=https://your-h5p-receiver.example/api/import
H5P_IMPORT_TOKEN=...
H5P_EDITOR_URL=...
```

After deployment, generate a Railway public domain and verify:

```text
https://your-api.up.railway.app/health
```

## Vercel deployment

Import the same repository into Vercel and set the **Root Directory** to:

```text
frontend
```

Add:

```text
NEXT_PUBLIC_API_URL=https://your-api.up.railway.app
```

Then redeploy.

## Main API routes

- `GET /health`
- `GET /api/activity-types`
- `POST /api/suggest`
- `POST /api/generate`
- `GET /api/jobs/{job_id}/h5p`
- `GET /api/jobs/{job_id}/qa`
- `POST /api/jobs/{job_id}/send-to-h5p`

Generated files are held temporarily on the Railway instance and expire automatically.

## Current H5P generation

The backend preserves the existing generator logic and templates for the recommended activities, including Quiz, Multiple Choice, Dialog Cards, Dictation, Page, Course Presentation, Interactive Book, Drag the Words, Fill in the Blanks, Mark the Words, Cornell Notes, Essay and Summary. Other installed templates use the existing generic JSON patch generator.
