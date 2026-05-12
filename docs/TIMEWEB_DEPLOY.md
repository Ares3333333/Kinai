# Timeweb Cloud Deploy

Goal: one public HTTPS link for testers and investors, with the site and API running from the same Python service.

## Recommended Path

Use **Timeweb Cloud App Platform -> Dockerfile**.

This repo already has the required `Dockerfile`:

- builds from `python:3.11-slim`;
- installs `requirements-site.txt`;
- exposes `8502`;
- starts `site_server.py`.

Timeweb will place the app behind Nginx and issue a Let's Encrypt certificate for the technical domain. Later you can attach your own domain.

## Important Data Rule

App Platform creates a new container on redeploy. Do not rely on local `data/*.jsonl` as the only production database.

For production tester data, set:

```text
STORAGE_BACKEND=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=replace-me
SUPABASE_EVENTS_TABLE=kinaesthetic_events
```

Raw video, audio, frames, and webcam screenshots must never be sent to Supabase or stored on Timeweb.

## Timeweb App Settings

Create an App Platform app from the GitHub repo:

```text
Deploy type: Dockerfile
Project directory: repository root
Health check path: /healthz
Port: detected from EXPOSE 8502
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

If LLM keys are absent, critical coach alerts still work through local templates.

## Public Checks After Deploy

Open these URLs on the Timeweb technical domain:

```text
https://your-timeweb-domain/
https://your-timeweb-domain/play
https://your-timeweb-domain/camera-check
https://your-timeweb-domain/demo
https://your-timeweb-domain/evidence
https://your-timeweb-domain/metrics
https://your-timeweb-domain/healthz
https://your-timeweb-domain/api/system-health
https://your-timeweb-domain/api/storage-health
```

## Tester Flow

Send testers this order:

1. `/camera-check`
2. `/play`

Send investors this order:

1. `/`
2. `/demo`
3. `/evidence`
4. `/play`

## If Using A Timeweb VDS Instead

VDS also works. It gives you a normal persistent disk, but Supabase is still better for structured tester data and backups.

Run the app with Docker:

```powershell
docker build -t kinaesthetic-ai .
docker run -d --name kinaesthetic-ai --restart unless-stopped --env-file .env.production -p 8502:8502 kinaesthetic-ai
```

Put Caddy or Nginx in front of it for HTTPS and proxy to `127.0.0.1:8502`.
