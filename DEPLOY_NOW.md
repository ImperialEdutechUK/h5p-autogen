# Deploy this repository to Railway and Vercel

## Railway

Use the repository root as the Railway service root.

Required variable:

- `LLM_API_KEY` = your OpenAI-compatible API key used by this app

Optional H5P integration variables:

- `H5P_IMPORT_ENDPOINT`
- `H5P_IMPORT_TOKEN`
- `H5P_EDITOR_URL`

Do not create a `.streamlit/secrets.toml` file on Railway. This version safely reads Railway environment variables first.

After deployment, go to Railway > Settings > Networking > Generate Domain and copy the public URL.

## Vercel

Import the same GitHub repository.

**Important:** set Vercel **Root Directory** to exactly:

`vercel-frontend`

Vercel must therefore see this file as its package file:

`vercel-frontend/package.json`

Add this Vercel environment variable:

- `NEXT_PUBLIC_RAILWAY_APP_URL` = the Railway public URL, for example `https://h5p-autogen-production.up.railway.app`

Then deploy/redeploy.

If Vercel says `No Next.js version detected`, the Root Directory is wrong. Set it to `vercel-frontend` and redeploy.
