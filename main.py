from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse


APP_ROOT = Path(__file__).resolve().parent
SITE_ROOT = APP_ROOT / "pitch_site"
STARTED_AT = time.time()
SESSION_SUMMARIES: dict[str, dict[str, Any]] = {}

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def env_value(name: str, fallback: str = "") -> str:
    return os.getenv(name, fallback).strip()


def env_port() -> int:
    raw = env_value("PORT", str(DEFAULT_PORT))
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port <= 65535 else DEFAULT_PORT


HOST = env_value("HOST", DEFAULT_HOST) or DEFAULT_HOST
PORT = env_port()


app = FastAPI(
    title="Kinaesthetic AI Public Site",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


ROUTES = {
    "/": "index.html",
    "/play": "play.html",
    "/demo": "demo.html",
    "/overlay": "overlay.html",
    "/evidence": "evidence.html",
    "/metrics": "metrics.html",
    "/privacy": "privacy.html",
    "/pitch": "pitch.html",
    "/report": "report.html",
    "/tester": "tester.html",
    "/validation": "validation.html",
    "/study": "study.html",
    "/share": "share.html",
    "/camera-check": "camera_check.html",
    "/admin": "admin.html",
    "/pitch-demo": "pitch_demo.html",
    "/data-room": "data_room.html",
}

WRITE_ROUTES = {
    "/api/consent": "consent",
    "/api/feedback": "tester_feedback",
    "/api/baseline-profile": "baseline_profile",
    "/api/study-event": "study_event",
    "/api/signals": "public_signal",
    "/api/session": "public_session",
    "/api/session-heartbeat": "session_heartbeat",
    "/api/camera-check": "camera_check",
    "/api/autolearn-run": "autolearn_run",
}

DEMO_STATE = {
    "mode": "demo",
    "is_live": False,
    "is_demo": True,
    "engine_connected": False,
    "camera_active": False,
    "session_status": "public_demo",
    "tilt_risk": 38,
    "readiness": 76,
    "recovery": 64,
    "jaw_tension": "low",
    "shoulder_tension": "low",
    "recommendation": "Ready",
    "alert_level": "normal",
    "confidence": 0.91,
    "signal_confidence": 0.91,
    "message": "Public demo mode. Browser CV runs locally when camera access is allowed.",
    "numbers_visible": True,
}

LOCAL_COMMANDS = [
    "Soften jaw. Drop shoulders. Long exhale.",
    "Release face. Sit tall. Widen your view.",
    "Shoulders lower. Eyes wide. One calm breath.",
    "Unclench jaw. Heavy elbows. Play the next moment.",
    "Reset posture. Exhale. Return to the next play.",
]

MEDIA_KEY_FRAGMENTS = (
    "raw_video",
    "raw_audio",
    "frame",
    "frames",
    "screenshot",
    "image",
    "media",
    "blob",
    "base64",
    "canvas",
)


@app.middleware("http")
async def security_headers(_request: Request, call_next):
    response = await call_next(_request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=()")
    return response


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def storage_status() -> dict[str, Any]:
    provider = env_value("STORAGE_BACKEND", "supabase").lower()
    supabase_url = env_value("SUPABASE_URL")
    supabase_key = env_value("SUPABASE_SERVICE_ROLE_KEY")
    table = env_value("SUPABASE_EVENTS_TABLE", "kinaesthetic_events")
    diagnostics: list[str] = []

    if provider != "supabase":
        diagnostics.append("STORAGE_BACKEND is not supabase")
    if not supabase_url:
        diagnostics.append("SUPABASE_URL is not set")
    elif not supabase_url.startswith(("https://", "http://")):
        diagnostics.append("SUPABASE_URL must start with https://")
    if not supabase_key:
        diagnostics.append("SUPABASE_SERVICE_ROLE_KEY is not set")

    configured = provider == "supabase" and not diagnostics
    return {
        "ok": True,
        "provider": "supabase" if configured else "local",
        "configured": configured,
        "table": table if configured else None,
        "diagnostics": diagnostics,
        "privacy": (
            "Only derived signals, labels, session metadata and camera diagnostics are stored. "
            "Raw video/audio/frames are never uploaded."
        ),
    }


def sanitize_payload(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[max_depth]"
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(fragment in lowered for fragment in MEDIA_KEY_FRAGMENTS):
                clean[key_text] = "[removed_raw_media]"
                continue
            clean[key_text] = sanitize_payload(item, depth + 1)
        return clean
    if isinstance(value, list):
        return [sanitize_payload(item, depth + 1) for item in value[:500]]
    if isinstance(value, str) and len(value) > 20_000:
        return value[:20_000] + "...[truncated]"
    return value


def event_from_payload(kind: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    clean_payload = sanitize_payload(payload if isinstance(payload, dict) else {})
    headers = headers or {}
    return {
        "event_type": kind,
        "session_id": clean_payload.get("session_id"),
        "tester_id": clean_payload.get("tester_id"),
        "game": clean_payload.get("game"),
        "source": clean_payload.get("source") or clean_payload.get("mode") or "fastapi_public_site",
        "payload": {
            **clean_payload,
            "received_at": utc_now_iso(),
            "server": "fastapi_public_site",
            "raw_media_stored": False,
            "user_agent": headers.get("user-agent", "")[:300],
        },
        "created_at": clean_payload.get("timestamp") or clean_payload.get("accepted_at") or utc_now_iso(),
    }


def mirror_to_supabase(kind: str, record: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    status = storage_status()
    if not status["configured"]:
        return {
            "mirrored": False,
            "provider": status["provider"],
            "reason": "not_configured",
            "diagnostics": status["diagnostics"],
        }

    supabase_url = env_value("SUPABASE_URL").rstrip("/")
    supabase_key = env_value("SUPABASE_SERVICE_ROLE_KEY")
    table = env_value("SUPABASE_EVENTS_TABLE", "kinaesthetic_events")
    event = event_from_payload(kind, record, headers=headers)
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/{table}",
        data=json.dumps(event, ensure_ascii=False).encode("utf-8"),
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.5) as response:
            return {
                "mirrored": 200 <= response.status < 300,
                "provider": "supabase",
                "status": response.status,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(300).decode("utf-8", errors="replace") if exc.fp else ""
        return {"mirrored": False, "provider": "supabase", "error": f"HTTP {exc.code}", "body": body}
    except Exception as exc:
        return {"mirrored": False, "provider": "supabase", "error": str(exc)[:300]}


def update_session_summary(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None
    summary = SESSION_SUMMARIES.setdefault(
        session_id,
        {
            "ok": True,
            "session_id": session_id,
            "samples": 0,
            "tilt_max": 0,
            "tilt_latest": 0,
            "readiness_latest": 0,
            "recovery_latest": 0,
            "recovery_delta": 0,
            "dominant_lock": "mixed",
            "raw_media_stored": False,
            "proof": {"headline": "Collecting proof"},
        },
    )
    summary["updated_at"] = utc_now_iso()
    if payload.get("tester_id"):
        summary["tester_id"] = payload.get("tester_id")
    if payload.get("game"):
        summary["game"] = payload.get("game")
    if kind in {"public_signal", "signal", "session_heartbeat"} or "tilt_risk" in payload:
        summary["samples"] = int(summary.get("samples") or 0) + 1
        tilt = float(payload.get("tilt_risk") or summary.get("tilt_latest") or 0)
        readiness = float(payload.get("readiness") or summary.get("readiness_latest") or 0)
        recovery = float(payload.get("recovery") or summary.get("recovery_latest") or 0)
        previous_max = float(summary.get("tilt_max") or 0)
        summary["tilt_latest"] = round(tilt)
        summary["readiness_latest"] = round(readiness)
        summary["recovery_latest"] = round(recovery)
        summary["tilt_max"] = round(max(previous_max, tilt))
        summary["recovery_delta"] = round(max(0, summary["tilt_max"] - tilt))
        jaw = str(payload.get("jaw_tension") or "").lower()
        shoulder = str(payload.get("shoulder_tension") or "").lower()
        if "high" in jaw or float(payload.get("jaw_score") or 0) >= 55:
            summary["dominant_lock"] = "jaw"
        elif "high" in shoulder or float(payload.get("shoulder_score") or 0) >= 55:
            summary["dominant_lock"] = "shoulders"
        elif tilt >= 45:
            summary["dominant_lock"] = "posture"
        samples = int(summary.get("samples") or 0)
        if samples >= 5:
            summary["proof"] = {"headline": f"Tilt {summary['tilt_max']} -> {summary['tilt_latest']}"}
        else:
            summary["proof"] = {"headline": "Collecting proof"}
    if kind == "public_session" and payload.get("action") == "end":
        summary["ended_at"] = utc_now_iso()
    return summary


def health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "kinaesthetic-fastapi",
        "host": HOST,
        "port": PORT,
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
    }


def json_ok(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload)


def file_response(relative_name: str, head: bool = False) -> Response:
    path = (SITE_ROOT / relative_name).resolve()
    try:
        path.relative_to(SITE_ROOT.resolve())
    except ValueError:
        return json_ok({"ok": False, "error": "invalid_path"})
    if not path.exists() or not path.is_file():
        return json_ok({"ok": False, "error": "not_found"})
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if head:
        size = path.stat().st_size
        return Response(status_code=200, media_type=media_type, headers={"Content-Length": str(size)})
    return FileResponse(path, media_type=media_type)


def local_command(payload: dict[str, Any]) -> str:
    tilt = float(payload.get("tilt_risk") or 0)
    jaw = str(payload.get("jaw_tension") or "").lower()
    shoulder = str(payload.get("shoulder_tension") or "").lower()
    if "high" in jaw or tilt >= 70:
        return "Unclench jaw. Exhale. Widen your view."
    if "high" in shoulder or tilt >= 55:
        return "Drop shoulders. Heavy elbows. Play the next moment."
    if tilt >= 40:
        return "Reset posture. One calm breath. Refocus."
    return LOCAL_COMMANDS[0]


def clean_command(text: str) -> str:
    command = " ".join(str(text or "").strip().split())
    if not command:
        return LOCAL_COMMANDS[0]
    return command[:140]


def groq_command(payload: dict[str, Any]) -> dict[str, Any] | None:
    api_key = env_value("GROQ_API_KEY")
    if not api_key:
        return None
    model = env_value("GROQ_MODEL", "llama-3.1-8b-instant")
    body = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 32,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an esports performance coach. Return exactly one short safe command "
                    "in English, max 10 words. No medical claims."
                ),
            },
            {"role": "user", "content": json.dumps(sanitize_payload(payload), ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=4.0) as response:
            data = json.loads(response.read().decode("utf-8"))
        command = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "provider": "groq", "model": model, "command": clean_command(command), "raw_media_stored": False}
    except Exception:
        return None


def gemini_command(payload: dict[str, Any]) -> dict[str, Any] | None:
    api_key = env_value("GEMINI_API_KEY")
    if not api_key:
        return None
    model = env_value("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = (
        "Return exactly one short safe esports coach command in English, max 10 words. "
        "No medical claims. Derived signals only: "
        + json.dumps(sanitize_payload(payload), ensure_ascii=False)
    )
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 32}}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=4.0) as response:
            data = json.loads(response.read().decode("utf-8"))
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        command = parts[0].get("text", "") if parts else ""
        return {"ok": True, "provider": "gemini", "model": model, "command": clean_command(command), "raw_media_stored": False}
    except Exception:
        return None


def coach_response(payload: dict[str, Any]) -> dict[str, Any]:
    for provider in (groq_command, gemini_command):
        result = provider(payload)
        if result:
            return result
    return {
        "ok": True,
        "provider": "local_fallback",
        "model": "local_command_library",
        "command": local_command(payload),
        "raw_media_stored": False,
    }


@app.get("/")
async def index() -> Response:
    return file_response("index.html")


@app.head("/")
async def index_head() -> Response:
    return file_response("index.html", head=True)


@app.get("/health")
@app.get("/healthz")
@app.get("/api/healthz")
async def health() -> JSONResponse:
    return json_ok(health_payload())


@app.head("/health")
@app.head("/healthz")
@app.head("/api/healthz")
async def health_head() -> Response:
    return Response(status_code=200)


def public_evidence() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "public_demo",
        "summary": "Public hosted demo is online. Live camera analysis runs locally in the browser.",
        "privacy": "Raw video is not uploaded or stored.",
    }


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return json_ok(DEMO_STATE)


@app.get("/api/session")
async def api_session(id: str | None = None) -> JSONResponse:
    if id and id in SESSION_SUMMARIES:
        return json_ok(SESSION_SUMMARIES[id])
    return json_ok(
        {
            "ok": True,
            "session_id": id,
            "samples": 0,
            "tilt_max": 0,
            "tilt_latest": 0,
            "readiness_latest": 0,
            "recovery_latest": 0,
            "recovery_delta": 0,
            "dominant_lock": "mixed",
            "raw_media_stored": False,
            "proof": {"headline": "Collecting proof"},
        }
    )


@app.get("/api/storage-health")
@app.get("/api/hosted-storage")
async def api_storage() -> JSONResponse:
    return json_ok(storage_status())


@app.get("/api/config-status")
async def config_status() -> JSONResponse:
    return json_ok(
        {
            "ok": True,
            "supabase_configured": bool(storage_status()["configured"]),
            "groq_configured": bool(env_value("GROQ_API_KEY")),
            "gemini_configured": bool(env_value("GEMINI_API_KEY")),
            "storage_table": env_value("SUPABASE_EVENTS_TABLE", "kinaesthetic_events"),
        }
    )


@app.get("/api/llm-health")
async def api_llm_health() -> JSONResponse:
    provider = "groq" if env_value("GROQ_API_KEY") else "gemini" if env_value("GEMINI_API_KEY") else "local_fallback"
    model = (
        env_value("GROQ_MODEL", "llama-3.1-8b-instant")
        if provider == "groq"
        else env_value("GEMINI_MODEL", "gemini-2.5-flash")
        if provider == "gemini"
        else "local_command_library"
    )
    return json_ok({"ok": True, "connected": provider != "local_fallback", "provider": provider, "status": "ready", "model": model})


@app.get("/api/system-health")
@app.get("/api/engine-health")
@app.get("/api/demo-readiness")
async def api_system_health() -> JSONResponse:
    return json_ok({"ok": True, "mode": "public_demo", "service": "kinaesthetic-fastapi", "storage": storage_status(), "healthy_for_demo": True})


@app.get("/api/evidence")
@app.get("/api/report")
@app.get("/api/investor-metrics")
@app.get("/api/cohort-summary")
async def api_public_evidence() -> JSONResponse:
    return json_ok(public_evidence())


@app.get("/api/export-founder-deck")
async def api_export_founder_deck() -> JSONResponse:
    return json_ok({"ok": True, "mode": "public_demo", "files": []})


@app.get("/api/performance-profile")
@app.get("/api/session-score")
@app.get("/api/coach-memory")
@app.get("/api/validation-mode")
async def api_empty_public_rows() -> JSONResponse:
    return json_ok({"ok": True, "mode": "public_demo", "rows": [], "summary": None})


@app.get("/api/privacy-passport")
async def api_privacy_passport() -> JSONResponse:
    return json_ok(
        {
            "ok": True,
            "raw_video_stored": False,
            "raw_audio_stored": False,
            "raw_frames_stored": False,
            "stored_data": ["derived_signals", "session_metadata", "tester_labels", "camera_diagnostics"],
            "message": "Browser CV runs locally. Public deploy stores only derived beta events.",
        }
    )


@app.get("/api/proof-card")
@app.get("/api/proof-share")
@app.get("/api/share-proof")
async def api_proof() -> JSONResponse:
    return json_ok({"ok": True, "mode": "public_demo", "title": "Recovery proof", "tilt_before": 74, "tilt_after": 31, "recovery_seconds": 18, "raw_media_stored": False})


@app.get("/api/data-room")
async def api_data_room() -> JSONResponse:
    return json_ok({"ok": True, "files": [], "metrics": {"tester_target": 100, "label_target": 150, "storage": storage_status()}})


@app.get("/api/session-replay")
@app.get("/api/command-effectiveness")
@app.get("/api/command-effectiveness-table")
@app.get("/api/false-alert-review")
@app.get("/api/validation-study-summary")
@app.get("/api/autolearn-status")
@app.get("/api/rollup-summary")
async def api_public_tables() -> JSONResponse:
    return json_ok({"ok": True, "mode": "public_demo", "rows": [], "recent": [], "summary": {}})


@app.post("/api/queue-flush")
async def api_queue_flush(request: Request) -> JSONResponse:
    payload = await request.json()
    rows = payload.get("events") if isinstance(payload, dict) and isinstance(payload.get("events"), list) else []
    mirrored = []
    headers = {k.lower(): v for k, v in request.headers.items()}
    for item in rows[:500]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("path") or "queued_event").strip("/").replace("/", "_")[:80]
        event_payload = item.get("payload") if isinstance(item.get("payload"), dict) else item
        update_session_summary(kind, event_payload)
        mirrored.append(mirror_to_supabase(kind, event_payload, headers=headers))
    return json_ok({"ok": True, "mode": "public_site", "stored": any(row.get("mirrored") for row in mirrored), "replay_count": len(mirrored), "storage": storage_status(), "raw_media_stored": False})


@app.post("/api/coach-command")
async def api_coach_command(request: Request) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        payload = {}
    return json_ok(coach_response(payload))


@app.post("/{path:path}")
async def api_write(path: str, request: Request) -> JSONResponse:
    api_path = "/" + path.rstrip("/")
    payload = await request.json()
    if not isinstance(payload, dict):
        payload = {}
    kind = WRITE_ROUTES.get(api_path, api_path.strip("/").replace("/", "_") or "event")
    headers = {k.lower(): v for k, v in request.headers.items()}
    if kind == "public_session" and not payload.get("session_id"):
        payload = {**payload, "session_id": f"web-{int(time.time() * 1000)}"}
    summary = update_session_summary(kind, payload)
    storage = mirror_to_supabase(kind, payload, headers=headers)
    response = {
        "ok": True,
        "mode": "public_site",
        "stored": bool(storage.get("mirrored")),
        "storage": storage,
        "raw_media_stored": False,
    }
    if kind == "public_session":
        response["session"] = {"session_id": payload.get("session_id")}
    if summary:
        response["summary"] = summary
    return json_ok(response)


@app.get("/{path:path}")
async def frontend_or_static(path: str) -> Response:
    route_path = "/" + path.rstrip("/")
    if route_path in ROUTES:
        return file_response(ROUTES[route_path])
    candidate = (SITE_ROOT / path).resolve()
    try:
        candidate.relative_to(SITE_ROOT.resolve())
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid_path"}, status_code=400)
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate, media_type=mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
    if route_path.startswith("/api/"):
        return json_ok({"ok": True, "mode": "public_demo", "service": "kinaesthetic-fastapi", "storage": storage_status()})
    return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)


@app.head("/{path:path}")
async def frontend_or_static_head(path: str) -> Response:
    route_path = "/" + path.rstrip("/")
    if route_path in ROUTES:
        return file_response(ROUTES[route_path], head=True)
    candidate = (SITE_ROOT / path).resolve()
    try:
        candidate.relative_to(SITE_ROOT.resolve())
    except ValueError:
        return Response(status_code=400)
    if candidate.exists() and candidate.is_file():
        return Response(status_code=200)
    if route_path.startswith("/api/"):
        return Response(status_code=200)
    return Response(status_code=404)
