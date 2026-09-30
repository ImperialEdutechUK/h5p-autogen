# Deploy H5P AutoGen with Railway + Vercel

## Architecture

- Railway runs the existing Python/Streamlit H5P generator.
- Vercel hosts the public Next.js entry page.
- The Vercel page loads the Railway application and also provides an **Open full app** fallback.
- AI/API secrets remain on Railway. Do not put `LLM_API_KEY`, `FREEPIK_API_KEY`, or `H5P_IMPORT_TOKEN` in Vercel `NEXT_PUBLIC_*` variables.

This preserves the current generator without rewriting the roughly 8,000-line Streamlit application.

## 1. Push the project to GitHub

Push the contents of this folder to a GitHub repository.

## 2. Deploy the generator to Railway

Create a Railway project and add a service from the GitHub repository.

Set the service root directory to the repository root (the folder containing `Dockerfile`, `h5p.py`, and `templates`). Railway will detect the root `Dockerfile`.

Add the required values from `.env.railway.example` in Railway -> Service -> Variables.

Minimum required variable:

```text
LLM_API_KEY=YOUR_KEY
```

If you use Freepik:

```text
FREEPIK_API_KEY=YOUR_KEY
```

For the H5P editor connection:

```text
H5P_IMPORT_ENDPOINT=https://your-h5p-host.example/api/h5p/import
H5P_IMPORT_TOKEN=YOUR_TOKEN_IF_REQUIRED
H5P_EDITOR_URL=https://your-h5p-host.example/editor
```

Do not manually create a `PORT` variable unless required. Railway supplies `PORT` and the Docker command listens on it.

Deploy the service. After deployment, generate a public Railway domain, for example:

```text
https://h5p-autogen-production.up.railway.app
```

Test this URL directly before continuing.

Health endpoint:

```text
/_stcore/health
```

## 3. Deploy the frontend to Vercel

Create a new Vercel project using the same GitHub repository.

Set **Root Directory** to:

```text
vercel-frontend
```

Framework should be detected as Next.js.

Add this Vercel environment variable:

```text
NEXT_PUBLIC_RAILWAY_APP_URL=https://YOUR-RAILWAY-DOMAIN.up.railway.app
```

Deploy the Vercel project.

The resulting Vercel URL becomes the public URL for users.

## 4. Important security rule

Keep these only on Railway:

- `LLM_API_KEY`
- `FREEPIK_API_KEY`
- `H5P_IMPORT_TOKEN`

Only the public Railway URL belongs in `NEXT_PUBLIC_RAILWAY_APP_URL` on Vercel.

## 5. H5P import feature

Deployment does not by itself create the H5P receiver. `H5P_IMPORT_ENDPOINT` still needs to point to your Moodle/WordPress/H5P receiver API.

The generator sends a multipart POST with the generated `.h5p` file in a field named `file`. The receiver should return JSON such as:

```json
{
  "edit_url": "https://your-h5p-host.example/editor/123"
}
```

The existing **Send to H5P** / **Open H5P Editor** buttons then use that URL.

## Local checks

### Railway app

```bash
docker build -t h5p-autogen .
docker run --rm -p 8080:8080 -e PORT=8080 -e LLM_API_KEY=YOUR_KEY h5p-autogen
```

Open `http://localhost:8080`.

### Vercel frontend

```bash
cd vercel-frontend
npm install
cp .env.example .env.local
npm run dev
```

Open `http://localhost:3000`.
