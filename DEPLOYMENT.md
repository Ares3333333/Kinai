# Kinaesthetic AI Deploy Checklist

The public site must run on HTTPS. Browser camera access (`getUserMedia`) works on `localhost` for local testing, but public tester and investor links must be `https://...`.

## Fastest Single-Host Shape: Timeweb Cloud

For the simplest public link, deploy this repo to **Timeweb Cloud App Platform** with the existing `Dockerfile`.

Use:

```text
Deploy type: Dockerfile
Project directory: repository root
Health check path: /healthz
```

Set production env vars:

```text
HOST=0.0.0.0
PORT=8502
STORAGE_BACKEND=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=replace-me
SUPABASE_EVENTS_TABLE=kinaesthetic_events
```

This serves `/`, `/play`, `/camera-check`, `/demo`, `/evidence`, `/metrics`, and `/api/*` from one HTTPS domain. Details are in `docs/TIMEWEB_DEPLOY.md`.

Important: do not rely on App Platform local files as the production database. Use Supabase for tester events, labels, proof metadata, and camera diagnostics.

## Alternative Production Shape

Use three pieces:

1. **Vercel** - static product site from `pitch_site`.
2. **Render / Railway / Fly** - Python backend from `site_server.py`.
3. **Supabase** - hosted database for derived events and labels.

Why: Vercel is excellent for the frontend, but the current Python backend expects a long-running process and writes JSONL. Hosted database mirroring makes tester data persistent.

## Frontend on Vercel

1. Import the repo into Vercel.
2. Keep the repo root as the project root.
3. Set:

```text
KAI_BACKEND_URL=https://your-backend-domain.onrender.com
```

4. Deploy.
5. Open:

```text
https://your-vercel-domain.vercel.app/
https://your-vercel-domain.vercel.app/play
https://your-vercel-domain.vercel.app/camera-check
https://your-vercel-domain.vercel.app/demo
https://your-vercel-domain.vercel.app/evidence
https://your-vercel-domain.vercel.app/metrics
```

The included `vercel.json` rewrites clean URLs to files inside `pitch_site`. `/api/*` is handled by `api/[...path].js`: when `KAI_BACKEND_URL` is set it proxies to the Python backend; when it is missing it returns honest public-demo fallback JSON.

## Backend on Render

Create a Render Web Service from this repo.

Recommended settings:

```text
Runtime: Docker
Health check path: /healthz
```

Environment variables:

```text
HOST=0.0.0.0
PORT=8502
STORAGE_BACKEND=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=replace-me
SUPABASE_EVENTS_TABLE=kinaesthetic_events
GROQ_API_KEY=replace-me
GROQ_MODEL=llama-3.1-8b-instant
GEMINI_API_KEY=replace-me
GEMINI_MODEL=gemini-2.5-flash
```

Public backend checks:

```text
https://your-backend-domain.onrender.com/healthz
https://your-backend-domain.onrender.com/api/state
https://your-backend-domain.onrender.com/api/system-health
https://your-backend-domain.onrender.com/api/storage-health
```

## Database Setup

Run the SQL in `docs/supabase_schema.sql` inside the Supabase SQL editor.

What is stored:

- session id, tester id, game, source, timestamp;
- derived body-state scores such as tilt risk, readiness, recovery, jaw/shoulder/posture scores;
- tester labels such as felt tilt, helped, false alert;
- proof metadata and session summaries.

What is never stored:

- raw video;
- raw audio;
- camera frames;
- screenshots from the webcam.

## 100+ Tester Base

The first investor milestone is:

```text
100+ testers
150+ feedback labels
recovery proof per useful command
false alert rate visible, not hidden
```

`/play` automatically saves derived samples after the player starts the session and consents. `/metrics` and `/evidence` show current progress toward the 100+ tester base.

Before sending `/play` to early testers, send `/camera-check` first. It records camera failure reasons such as blocked permission, no HTTPS, camera busy, or no camera found. Raw video is still never stored.

## Final Manual Test

Run this before sending links to testers or investors:

1. Open `/`.
2. Open `/demo`, click `Показать demo`, confirm the 4-step story ends with proof.
3. Open `/play`, click `Старт с камерой`, accept consent, confirm the page becomes LIVE.
4. Open `/camera-check`, run the check, confirm it shows camera ready or a clear fix.
5. Open `/metrics`, confirm the 100+ tester base and storage status are clear.
6. Open `/evidence`, confirm no mojibake and clear privacy/evidence copy.
7. Open `/evidence`, click `Export package`, confirm JSON downloads without raw media.
8. Open `/api/system-health` and `/api/storage-health`, confirm JSON responds.

Local command:

```powershell
.\.venv\Scripts\python.exe tools\pre_demo_check.py
npm run test:e2e
```
