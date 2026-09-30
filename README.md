# H5P AutoGen - Full Project

No Streamlit. The project contains:

- FastAPI backend for PDF extraction, AI generation, H5P packaging and QA reports.
- OpenRouter-compatible LLM configuration.
- 29 H5P template packages under `templates/`.
- Playwright automation for publishing generated `.h5p` files to `imperiallearning.h5p.com`.
- Next.js frontend under `frontend/` for Vercel.

## Railway

Deploy the repository root to Railway.

The Dockerfile installs the Python requirements, Playwright and Chromium, then starts:

`uvicorn api:app --host 0.0.0.0 --port $PORT`

Copy the values from `.env.railway.example` into Railway Variables and replace placeholders.

Important variables:

- `LLM_API_KEY`
- `LLM_API_BASE=https://openrouter.ai/api/v1`
- `LLM_MODEL=openai/gpt-4.1-mini`
- `FRONTEND_ORIGINS=*` while testing
- `H5P_CREATE_URL=https://imperiallearning.h5p.com/content/create`
- `H5P_TEST_TARGET_URL=https://imperiallearning.h5p.com/content/1292811530103001337`
- `H5P_USERNAME`
- `H5P_PASSWORD`

## Vercel

Create/deploy the same GitHub repository in Vercel and set the Root Directory to:

`frontend`

Set:

`NEXT_PUBLIC_API_URL=https://YOUR-RAILWAY-DOMAIN.up.railway.app`

Then redeploy.

## Checks

Backend health:

`GET /health`

Activity types:

`GET /api/activity-types`

H5P browser automation check:

`GET /api/h5p/automation-check`

The frontend publishes generated jobs with:

`POST /api/jobs/{job_id}/publish-to-h5p`

## Security

Do not commit real API keys or H5P credentials to GitHub. Store them only in Railway/Vercel environment variables. Rotate any credential that has been exposed in screenshots or commits.
