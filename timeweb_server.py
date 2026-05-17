from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse


APP_ROOT = Path(__file__).resolve().parent
SITE_ROOT = APP_ROOT / "pitch_site"


def _env_port() -> int:
    raw = os.getenv("PORT", "8080").strip()
    try:
        port = int(raw)
    except ValueError:
        print(f"Invalid PORT={raw!r}; using 8080", flush=True)
        return 8080
    if port <= 0 or port > 65535:
        print(f"Out-of-range PORT={raw!r}; using 8080", flush=True)
        return 8080
    return port


def _bind_host() -> str:
    explicit = os.getenv("KAI_BIND_HOST", "").strip()
    if explicit:
        return explicit
    raw = os.getenv("HOST", "").strip()
    if raw and raw not in {"0.0.0.0", "::", "localhost", "127.0.0.1"}:
        print(f"Ignoring HOST={raw!r}; binding to 0.0.0.0 for container runtime.", flush=True)
    return "0.0.0.0"


HOST = _bind_host()
PORT = _env_port()
STARTED_AT = time.time()
VISITOR_COOKIE = "kai_visitor_id"

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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def env_value(name: str, fallback: str = "") -> str:
    return os.getenv(name, fallback).strip()


def storage_status() -> dict:
    provider = env_value("STORAGE_BACKEND", "local").lower()
    url = env_value("SUPABASE_URL")
    key = env_value("SUPABASE_SERVICE_ROLE_KEY")
    table = env_value("SUPABASE_EVENTS_TABLE", "kinaesthetic_events")
    configured = provider == "supabase" and url.startswith(("https://", "http://")) and bool(key)
    diagnostics = []
    if provider != "supabase":
        diagnostics.append("STORAGE_BACKEND is not supabase")
    if not url:
        diagnostics.append("SUPABASE_URL is not set")
    if not key:
        diagnostics.append("SUPABASE_SERVICE_ROLE_KEY is not set")
    return {
        "ok": True,
        "provider": "supabase" if configured else "local",
        "configured": configured,
        "table": table if configured else None,
        "diagnostics": diagnostics,
        "privacy": "Only derived signals, labels, session metadata and camera diagnostics are stored. Raw video/audio/frames are never uploaded.",
    }


def event_from_payload(kind: str, payload: dict, headers=None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    return {
        "event_type": kind,
        "session_id": payload.get("session_id"),
        "tester_id": payload.get("tester_id"),
        "game": payload.get("game"),
        "source": payload.get("source") or payload.get("mode") or "timeweb_public_site",
        "payload": {
            **payload,
            "received_at": utc_now_iso(),
            "server": "timeweb_public_site",
            "raw_media_stored": False,
            "user_agent": headers.get("User-Agent", "")[:300] if headers else "",
        },
        "created_at": payload.get("timestamp") or payload.get("accepted_at") or utc_now_iso(),
    }


def mirror_to_supabase(kind: str, payload: dict, headers=None) -> dict:
    status = storage_status()
    if not status["configured"]:
        return {"mirrored": False, "provider": status["provider"], "reason": "not_configured"}
    supabase_url = env_value("SUPABASE_URL").rstrip("/")
    supabase_key = env_value("SUPABASE_SERVICE_ROLE_KEY")
    table = env_value("SUPABASE_EVENTS_TABLE", "kinaesthetic_events")
    event = event_from_payload(kind, payload, headers=headers)
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
            return {"mirrored": 200 <= response.status < 300, "provider": "supabase", "status": response.status}
    except urllib.error.HTTPError as exc:
        body = exc.read(300).decode("utf-8", errors="replace") if exc.fp else ""
        return {"mirrored": False, "provider": "supabase", "error": f"HTTP {exc.code}", "body": body}
    except Exception as exc:
        return {"mirrored": False, "provider": "supabase", "error": str(exc)[:300]}


def fetch_supabase_events(limit: int = 5000) -> list[dict]:
    status = storage_status()
    if not status["configured"]:
        return []
    supabase_url = env_value("SUPABASE_URL").rstrip("/")
    supabase_key = env_value("SUPABASE_SERVICE_ROLE_KEY")
    table = env_value("SUPABASE_EVENTS_TABLE", "kinaesthetic_events")
    params = urlencode(
        {
            "select": "event_type,session_id,tester_id,game,payload,created_at",
            "order": "created_at.desc",
            "limit": str(max(1, min(limit, 10000))),
        }
    )
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/{table}?{params}",
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=4.0) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def safe_number(value, fallback: float = 0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number else fallback


def evidence_from_events(events: list[dict]) -> dict:
    storage = storage_status()
    tester_ids = set()
    session_ids = set()
    label_count = 0
    helped_count = 0
    false_alert_count = 0
    signal_events = []
    tilt_values = []
    readiness_values = []
    recovery_values = []
    label_event_types = {"tester_feedback", "study_event", "site_visit"}

    for row in events:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        event_type = str(row.get("event_type") or payload.get("event_type") or "")
        tester_id = str(row.get("tester_id") or payload.get("tester_id") or "").strip()
        if tester_id:
            tester_ids.add(tester_id)
        session_id = str(row.get("session_id") or payload.get("session_id") or "").strip()
        if session_id:
            session_ids.add(session_id)
        if event_type in label_event_types:
            label_count += 1
            label = str(payload.get("kind") or payload.get("label") or payload.get("event") or "").lower()
            if "help" in label:
                helped_count += 1
            if "false" in label:
                false_alert_count += 1
        if event_type == "public_signal":
            signal_events.append(row)
            tilt_values.append(safe_number(payload.get("tilt_risk")))
            readiness_values.append(safe_number(payload.get("readiness")))
            recovery_values.append(safe_number(payload.get("recovery")))

    latest_signals = list(reversed(signal_events[:12]))
    timeline_points = []
    for index, row in enumerate(latest_signals):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        timeline_points.append(
            {
                "t": index * 8,
                "tilt_risk": round(safe_number(payload.get("tilt_risk"))),
                "readiness": round(safe_number(payload.get("readiness"))),
                "recovery": round(safe_number(payload.get("recovery"))),
            }
        )

    sample_count = len(signal_events)
    latest_payload = signal_events[0].get("payload", {}) if signal_events and isinstance(signal_events[0].get("payload"), dict) else {}
    tilt_max = round(max(tilt_values) if tilt_values else safe_number(latest_payload.get("tilt_risk")))
    tilt_latest = round(safe_number(latest_payload.get("tilt_risk")))
    avg_readiness = round(sum(readiness_values) / len(readiness_values), 1) if readiness_values else None
    help_rate = round((helped_count / label_count) * 100, 1) if label_count else None
    false_alert_rate = round((false_alert_count / label_count) * 100, 1) if label_count else None
    return {
        "ok": True,
        "mode": "public_site",
        "public_demo_mode": False,
        "summary": "Hosted beta is connected to Supabase. Evidence is calculated from stored derived events.",
        "privacy": "Raw video/audio/frames are never uploaded or stored.",
        "hosted_storage": storage,
        "public_session": {
            "samples": sample_count,
            "sessions": len(session_ids),
            "tilt_max": tilt_max,
            "tilt_latest": tilt_latest,
            "recovery_delta": max(0, tilt_max - tilt_latest),
            "avg_readiness": avg_readiness,
            "proof": {
                "headline": f"{sample_count} derived samples stored" if sample_count else "Collecting proof",
                "details": "Evidence is based on derived browser CV signals only. No raw media is stored.",
            },
        },
        "investor_metrics": {
            "body_state_samples": sample_count,
            "embedding_vectors": 0,
            "feedback_labels": label_count,
            "sessions": len(session_ids),
            "stored_events": len(events),
        },
        "cohort": {
            "total_alerts_labelled": label_count,
            "unique_testers": len(tester_ids),
            "tester_target": 100,
            "labels_target": 150,
            "help_rate_percent": help_rate,
            "false_alert_rate_percent": false_alert_rate,
        },
        "cloud_coach": {
            "provider": "groq" if env_value("GROQ_API_KEY") else "gemini" if env_value("GEMINI_API_KEY") else "fallback",
            "model": env_value("GROQ_MODEL", "llama-3.1-8b-instant") if env_value("GROQ_API_KEY") else env_value("GEMINI_MODEL", "gemini-2.5-flash") if env_value("GEMINI_API_KEY") else "local templates",
            "status": "ready" if env_value("GROQ_API_KEY") or env_value("GEMINI_API_KEY") else "fallback",
            "latency_ms": None,
        },
        "public_timeline": {"points": timeline_points},
        "next_milestone": {"tester_target": 100, "labels_target": 150},
    }


def public_evidence() -> dict:
    events = fetch_supabase_events()
    if events:
        return evidence_from_events(events)
    storage = storage_status()
    return {
        "ok": True,
        "mode": "public_demo",
        "public_demo_mode": not storage["configured"],
        "summary": "Public hosted demo is online. Live camera analysis runs locally in the browser.",
        "privacy": "Raw video is not uploaded or stored.",
        "hosted_storage": storage,
        "investor_metrics": {"body_state_samples": 0, "embedding_vectors": 0, "feedback_labels": 0, "stored_events": 0},
        "cohort": {
            "total_alerts_labelled": 0,
            "unique_testers": 0,
            "tester_target": 100,
            "labels_target": 150,
            "help_rate_percent": None,
            "false_alert_rate_percent": None,
        },
        "public_timeline": {"points": []},
        "cloud_coach": {
            "provider": "groq" if env_value("GROQ_API_KEY") else "gemini" if env_value("GEMINI_API_KEY") else "fallback",
            "model": env_value("GROQ_MODEL", "llama-3.1-8b-instant") if env_value("GROQ_API_KEY") else env_value("GEMINI_MODEL", "gemini-2.5-flash") if env_value("GEMINI_API_KEY") else "local templates",
            "status": "ready" if env_value("GROQ_API_KEY") or env_value("GEMINI_API_KEY") else "fallback",
            "latency_ms": None,
        },
    }


class TimewebHandler(BaseHTTPRequestHandler):
    server_version = "KinaestheticTimeweb/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def _send_bytes(self, status: int, body: bytes, content_type: str, extra_headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(self), microphone=()")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        self._send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _send_file(self, path: Path, extra_headers=None) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send_bytes(200, path.read_bytes(), content_type, extra_headers=extra_headers)

    def _visitor_id(self) -> tuple[str, bool]:
        cookies = self.headers.get("Cookie", "")
        for chunk in cookies.split(";"):
            name, _, value = chunk.strip().partition("=")
            if name == VISITOR_COOKIE and value and len(value) <= 80:
                return value, False
        return f"visitor-{uuid.uuid4().hex[:16]}", True

    def _track_page_visit(self, path: str) -> dict:
        visitor_id, is_new_cookie = self._visitor_id()
        visit_id = f"visit-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        mirror_to_supabase(
            "site_visit",
            {
                "session_id": visit_id,
                "tester_id": visit_id,
                "visitor_id": visitor_id,
                "path": path,
                "source": "site_visit",
                "mode": "public_site",
                "label": "site_visit",
                "timestamp": utc_now_iso(),
                "raw_media_stored": False,
            },
            headers=self.headers,
        )
        if not is_new_cookie:
            return {}
        return {"Set-Cookie": f"{VISITOR_COOKIE}={visitor_id}; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Lax"}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in {"/health", "/healthz", "/api/healthz"}:
            self._send_json(
                {
                    "ok": True,
                    "service": "kinaesthetic-timeweb",
                    "host": HOST,
                    "port": PORT,
                    "uptime_seconds": round(time.time() - STARTED_AT, 1),
                    "storage": storage_status(),
                }
            )
            return

        if path == "/api/state":
            self._send_json(DEMO_STATE)
            return

        if path in {"/api/storage-health", "/api/hosted-storage"}:
            self._send_json(storage_status())
            return

        if path == "/api/llm-health":
            self._send_json(
                {
                    "ok": True,
                    "connected": False,
                    "provider": "local_fallback",
                    "status": "fallback",
                    "model": "local_command_library",
                    "latency_ms": 0,
                    "message": "Public deploy uses local coach commands for critical alerts.",
                }
            )
            return

        if path in {"/api/system-health", "/api/engine-health", "/api/demo-readiness"}:
            self._send_json(
                {
                    "ok": True,
                    "mode": "public_demo",
                    "service": "kinaesthetic-timeweb",
                    "storage": storage_status(),
                    "healthy_for_demo": True,
                }
            )
            return

        if path in {"/api/evidence", "/api/report", "/api/investor-metrics", "/api/cohort-summary"}:
            self._send_json(public_evidence())
            return

        if path == "/api/export-founder-deck":
            self._send_json({"ok": True, "mode": "public_demo", "files": []})
            return

        if path in {"/api/performance-profile", "/api/session-score", "/api/coach-memory", "/api/validation-mode"}:
            self._send_json({"ok": True, "mode": "public_demo", "rows": [], "summary": None})
            return

        route_file = ROUTES.get(path)
        if route_file:
            self._send_file(SITE_ROOT / route_file, extra_headers=self._track_page_visit(path))
            return

        candidate = (SITE_ROOT / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(SITE_ROOT.resolve())
        except ValueError:
            self._send_json({"ok": False, "error": "invalid_path"}, 400)
            return
        self._send_file(candidate)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        raw = self.rfile.read(min(length, 2_000_000))
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        payload = self._read_json_body()

        if path == "/api/queue-flush":
            rows = payload.get("events") if isinstance(payload.get("events"), list) else []
            mirrored = []
            for item in rows[:500]:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or item.get("path") or "queued_event").strip("/")[:80]
                event_payload = item.get("payload") if isinstance(item.get("payload"), dict) else item
                mirrored.append(mirror_to_supabase(kind, event_payload, headers=self.headers))
            self._send_json({"ok": True, "stored": any(row.get("mirrored") for row in mirrored), "replayed": len(mirrored), "storage": storage_status()})
            return

        kind = WRITE_ROUTES.get(path, path.strip("/").replace("/", "_") or "event")
        storage = mirror_to_supabase(kind, payload, headers=self.headers)
        self._send_json(
            {
                "ok": True,
                "mode": "public_site",
                "stored": bool(storage.get("mirrored")),
                "storage": storage,
                "raw_media_stored": False,
            }
        )


def main() -> None:
    SITE_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"Starting Kinaesthetic AI Timeweb server on {HOST}:{PORT}", flush=True)
    print(f"Health: http://{HOST}:{PORT}/health", flush=True)
    print(f"Storage: {json.dumps(storage_status(), ensure_ascii=False)}", flush=True)
    ThreadingHTTPServer((HOST, PORT), TimewebHandler).serve_forever()


if __name__ == "__main__":
    main()
