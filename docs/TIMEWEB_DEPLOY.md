# Timeweb Cloud Deploy

Goal: one public HTTPS link for testers and investors, with the site and API running from the same lightweight Python service.

## Use This Server (Not site_server.py)

Timeweb App Platform must run **`timeweb_server.py`** — stdlib-only, binds `0.0.0.0:$PORT`, serves `pitch_site/` and public API routes.

Do **not** set the start command to `site_server.py` on Timeweb. That server imports `product_intelligence`, `scoring`, and other modules that are **not** installed by `requirements-site.txt`, so the container will crash or never pass health checks.

## Recommended Path

**Timeweb Cloud App Platform → Deploy from Dockerfile** (repo root).

The repo `Dockerfile`:

- base image `python:3.11-slim`
- installs `requirements-site.txt` (stdlib-only; no extra pip packages)
- `EXPOSE 8080`
- `CMD ["python", "-u", "timeweb_server.py"]`

Timeweb terminates TLS and proxies to your container port.

## Timeweb UI Settings (Exact)

| Setting | Value |
|--------|--------|
| Deploy type | Dockerfile |
| Project directory | repository root |
| **Port** | `8080` |
| **Health check path** | `/health` (also works: `/healthz`) |
| **Start command** | leave **empty** so Dockerfile `CMD` runs, **or** `python -u timeweb_server.py` |

Commit to deploy: `b0231ee` — *Use stable Timeweb server with Supabase writes* (`b0231eefd4b80b2a04fc3218dd3655d9d8a3c1c3`).

### Environment variables

```text
HOST=0.0.0.0
PORT=8080
STORAGE_BACKEND=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=replace-me
SUPABASE_EVENTS_TABLE=kinaesthetic_events
KAI_WRITE_AUTH=off
```

Optional LLM keys (not required for `/play` critical alerts — browser + local templates):

```text
GROQ_API_KEY=
GROQ_MODEL=llama-3.1-8b-instant
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
```

Raw video, audio, frames, and webcam screenshots must **never** be sent to Supabase or stored on Timeweb.

Apply `docs/supabase_schema.sql` in the Supabase SQL editor before expecting writes to succeed.

## Health Check

`GET /health` returns **200** JSON, for example:

```json
{
  "ok": true,
  "service": "kinaesthetic-timeweb",
  "host": "0.0.0.0",
  "port": 8080,
  "storage": { "provider": "supabase", "configured": true, ... }
}
```

`GET /api/storage-health` returns storage configuration only.

## Dockerfile HEALTHCHECK vs Timeweb

The image may ship **without** a Docker `HEALTHCHECK` instruction. Rely on **Timeweb platform** health probes to `http://<container>:8080/health`. A duplicate Docker `HEALTHCHECK` is optional and not required for App Platform.

## Public Checks After Deploy

```text
https://your-timeweb-domain/health
https://your-timeweb-domain/
https://your-timeweb-domain/play
https://your-timeweb-domain/camera-check
https://your-timeweb-domain/demo
https://your-timeweb-domain/api/storage-health
```

Smoke POST (no raw media):

```bash
curl -sS -X POST https://your-timeweb-domain/api/session \
  -H "Content-Type: application/json" \
  -d '{"session_id":"smoke","tester_id":"deploy","game":"timeweb"}'
```

Expect `200` with `"ok": true` and `"raw_media_stored": false`.

## If Health Stays "starting"

1. **Runtime logs** — first lines should include `Starting Kinaesthetic AI Timeweb server on 0.0.0.0:8080` and `Health: http://0.0.0.0:8080/health`. If you see import errors (`product_intelligence`, `scoring`), the start command is wrong (`site_server.py`).
2. **Port** — must be `8080` (matches `PORT` env and `EXPOSE`).
3. **Health path** — `/health` (not `/` and not a Streamlit port).
4. **Redeploy** after fixing UI; confirm Git commit is `b0231ee` or newer with `timeweb_server.py` in the image.

## Tester / Investor Flow

Testers: `/camera-check` → `/play`

Investors: `/` → `/demo` → `/evidence` → `/play`

## VDS (Optional)

```powershell
docker build -t kinaesthetic-ai .
docker run -d --name kinaesthetic-ai --restart unless-stopped --env-file .env.production -p 8080:8080 kinaesthetic-ai
```

Proxy HTTPS to `127.0.0.1:8080`.
