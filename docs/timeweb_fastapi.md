# Timeweb FastAPI Deploy

This is the non-Docker FastAPI entrypoint for Timeweb App Platform.

## Timeweb Deploy Without Dockerfile

```text
Environment: Python / FastAPI
Project directory path: empty
Entrypoint: main.py
Healthcheck path: /
Alternative healthcheck path: /health
```

If Timeweb asks for a start command:

```text
uvicorn main:app --host 0.0.0.0 --port 8080
```

## Required Timeweb Variables

Set these in Timeweb environment variables. Do not commit real values.

```text
HOST=0.0.0.0
PORT=8080
STORAGE_BACKEND=supabase
SUPABASE_URL=<set in Timeweb>
SUPABASE_SERVICE_ROLE_KEY=<set in Timeweb>
SUPABASE_EVENTS_TABLE=kinaesthetic_events
GROQ_API_KEY=<optional, set in Timeweb>
GROQ_MODEL=llama-3.1-8b-instant
GEMINI_API_KEY=<optional, set in Timeweb>
GEMINI_MODEL=gemini-2.5-flash
AI_WRITE_AUTH=<optional, set in Timeweb>
```

## Security

Never commit real API keys.
Never expose `SUPABASE_SERVICE_ROLE_KEY` to frontend files.
Never put keys into HTML, JS, CSS, docs, README, tests, screenshots, or issue text.
Rotate keys if they were exposed in screenshots or chat.

## Local Test Commands

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080
curl http://127.0.0.1:8080/
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/api/state
curl http://127.0.0.1:8080/api/config-status
```

## Privacy

Browser camera analysis stays local. The backend stores only derived signals,
labels, consent events, tester feedback, session metadata, camera diagnostics,
and public demo events. Raw video, raw audio, frames, screenshots, and media
blobs are never stored.
