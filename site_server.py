from __future__ import annotations

from force_utf8 import force_utf8

force_utf8()

import json
import mimetypes
import os
import ssl
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import product_intelligence as intel
import scoring
import security
from engine_status import heartbeat_age_seconds, read_engine_heartbeat


APP_ROOT = Path(__file__).resolve().parent
SITE_ROOT = APP_ROOT / "pitch_site"
OVERLAY_STATE_PATH = APP_ROOT / "data" / "overlay_state.json"
DATA_DIR = APP_ROOT / "data"
PORT = int(os.getenv("PORT", "8502"))
HOST = os.getenv("HOST", "0.0.0.0" if os.getenv("PORT") else "localhost")
SSL_CERT_FILE = os.getenv("SSL_CERT_FILE", "").strip()
SSL_KEY_FILE = os.getenv("SSL_KEY_FILE", "").strip()
LIVE_MAX_AGE_SECONDS = 2.0
STALE_MAX_AGE_SECONDS = 3.0
HEARTBEAT_MAX_AGE_SECONDS = 3.0

# Endpoints that mutate state and therefore require write-token + consent.
WRITE_ENDPOINTS = {
    "/api/feedback",
    "/api/baseline-profile",
    "/api/study-event",
    "/api/coach",
    "/api/signals",
    "/api/session",
    "/api/session-heartbeat",
    "/api/camera-check",
    "/api/queue-flush",
    "/api/autolearn-run",
    "/api/consent",
}

# Old API names that are still served but should be migrated to the new
# canonical names. Each value maps the legacy path to its successor.
DEPRECATED_ROUTES: dict[str, str] = {
    "/api/export-pitch-package": "/api/export-founder-deck",
    "/api/proof-card": "/api/share-proof",
    "/api/pitch-demo-script": "/api/share-proof",
}
DEPRECATION_SUNSET = "Wed, 01 Sep 2026 00:00:00 GMT"

DEMO_STATE = {
    "mode": "demo",
    "is_live": False,
    "is_demo": True,
    "last_updated_at": None,
    "age_seconds": None,
    "engine_connected": False,
    "camera_active": False,
    "signal_confidence": 0.91,
    "message": "Demo mode: this is a product interface simulation.",
    "session_status": "demo",
    "tilt_risk": 38,
    "readiness": 76,
    "recovery": 64,
    "jaw_tension": "low",
    "shoulder_tension": "low",
    "recommendation": "Ready",
    "alert_level": "normal",
    "confidence": 0.91,
}

_last_valid_raw_state: dict | None = None
_last_valid_normalized_state: dict | None = None


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def as_float(value, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def band(value: float) -> str:
    if value >= 70:
        return "high"
    if value >= 40:
        return "medium"
    return "low"


def parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fallback_state() -> dict:
    return dict(DEMO_STATE)


def offline_state(message: str = "Engine is not connected. Start app.py or use demo mode.") -> dict:
    state = fallback_state()
    state.update(
        {
            "mode": "offline",
            "is_live": False,
            "is_demo": False,
            "engine_connected": False,
            "camera_active": False,
            "signal_confidence": 0.0,
            "session_status": "offline",
            "tilt_risk": None,
            "readiness": None,
            "recovery": None,
            "jaw_tension": None,
            "shoulder_tension": None,
            "recommendation": "Start the live engine or use demo mode.",
            "alert_level": "offline",
            "confidence": 0.0,
            "message": message,
        }
    )
    return state


def read_overlay_state() -> tuple[dict | None, bool]:
    global _last_valid_raw_state
    if not OVERLAY_STATE_PATH.exists():
        return None, False
    try:
        raw = json.loads(OVERLAY_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _last_valid_raw_state, bool(_last_valid_raw_state)
    if isinstance(raw, dict):
        _last_valid_raw_state = raw
        return raw, True
    return _last_valid_raw_state, bool(_last_valid_raw_state)


def normalize_state(raw: dict | None, from_live_file: bool) -> dict:
    global _last_valid_normalized_state
    heartbeat = read_engine_heartbeat(DATA_DIR)
    heartbeat_age = heartbeat_age_seconds(DATA_DIR)
    heartbeat_fresh = heartbeat_age is not None and heartbeat_age <= HEARTBEAT_MAX_AGE_SECONDS
    if not raw:
        state = offline_state(
            "Engine connected, session is not running."
            if heartbeat_fresh
            else "Engine is not connected. Start app.py or use demo mode."
        )
        if heartbeat_fresh:
            state.update(
                {
                    "engine_connected": True,
                    "engine_heartbeat_age_seconds": round(heartbeat_age, 1),
                    "session_status": "idle",
                    "message": "Engine connected, but live session is not running.",
                }
            )
        return state

    metrics = raw.get("metrics") or {}
    recovery_block = raw.get("recovery") or {}
    prediction = raw.get("prediction") or {}

    tilt = clamp(as_float(raw.get("tilt_score"), 0.0))
    jaw = clamp(as_float(metrics.get("jaw_clench_score"), 0.0))
    shoulders = clamp(as_float(metrics.get("shoulder_elevation_score"), 0.0))
    recovery = clamp(as_float(recovery_block.get("score"), metrics.get("recovery_score", 0.0)))
    face_quality = clamp(as_float(raw.get("face_tracking_quality"), 0.0))

    confidence = clamp(face_quality) / 100.0
    if metrics:
        confidence = max(confidence, 0.55)
    if not raw.get("running"):
        confidence = min(confidence, 0.62)

    readiness = scoring.readiness_score(tilt, jaw, shoulders, recovery)

    alert = str(raw.get("coach_alert") or "").strip()
    recommendation = alert or "Ready"

    if confidence < 0.45:
        alert_level = "low_signal"
        recommendation = "Improve lighting / face the camera"
    elif recovery_block.get("active") or recovery >= 55:
        alert_level = "recovery"
        recommendation = alert or "Stay loose"
    elif tilt >= 65 or prediction.get("seconds") is not None or alert:
        alert_level = "warning"
        recommendation = alert or "Soften jaw ? drop shoulders"
    else:
        alert_level = "normal"

    updated_at = parse_iso_timestamp(raw.get("updated_at"))
    stale_seconds = None
    if updated_at:
        stale_seconds = max(
            0.0,
            (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds(),
        )
    file_age = None
    if OVERLAY_STATE_PATH.exists():
        try:
            file_age = max(0.0, datetime.now(timezone.utc).timestamp() - OVERLAY_STATE_PATH.stat().st_mtime)
        except OSError:
            file_age = stale_seconds
    age_seconds = file_age if file_age is not None else stale_seconds
    is_live_age = age_seconds is not None and age_seconds <= LIVE_MAX_AGE_SECONDS
    is_stale = age_seconds is not None and age_seconds > STALE_MAX_AGE_SECONDS
    engine_connected = heartbeat_fresh or (from_live_file and is_live_age)
    camera_active = bool(raw.get("running")) and is_live_age

    if heartbeat_fresh and not heartbeat.get("running"):
        mode = "offline"
        session_status = "idle"
        camera_active = False
        confidence = min(confidence, 0.45)
        alert_level = "offline"
        recommendation = "Engine connected, session is not running."
        message = "Engine connected, but live session is not running."
    elif not from_live_file:
        mode = "offline"
        session_status = "offline"
        message = "Engine is not connected. Start app.py or use demo mode."
    elif is_stale:
        mode = "stale"
        session_status = "cached"
        camera_active = False
        confidence = min(confidence, 0.45)
        if alert_level != "low_signal":
            alert_level = "low_signal"
        recommendation = "Live engine is not updating. Start a session or use demo mode."
        message = "Live engine is not updating. Stale metrics are hidden."
    elif raw.get("running") and is_live_age:
        mode = "live"
        session_status = "live"
        message = "LIVE ? analyzing body and face."
    else:
        mode = "offline"
        session_status = "idle"
        camera_active = False
        confidence = min(confidence, 0.45)
        alert_level = "offline"
        recommendation = "Engine connected, session is not running."
        message = "Engine connected, but live session is not running."

    metrics_are_live = mode == "live"
    numbers_visible = metrics_are_live and confidence >= 0.55
    state = {
        "mode": mode,
        "is_live": mode == "live",
        "is_demo": mode == "demo",
        "numbers_visible": numbers_visible,
        "last_updated_at": raw.get("updated_at"),
        "age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "engine_connected": engine_connected,
        "camera_active": camera_active,
        "engine_heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
        "signal_confidence": round(confidence, 2),
        "message": message,
        "session_status": session_status,
        "tilt_risk": round(tilt) if metrics_are_live else None,
        "readiness": round(readiness) if metrics_are_live else None,
        "recovery": round(recovery) if metrics_are_live else None,
        "jaw_tension": band(jaw) if metrics_are_live else None,
        "shoulder_tension": band(shoulders) if metrics_are_live else None,
        "recommendation": recommendation,
        "alert_level": alert_level,
        "confidence": round(confidence, 2),
        "updated_at": raw.get("updated_at"),
        "stale_seconds": round(stale_seconds, 1) if stale_seconds is not None else None,
        "raw": {
            "jaw_score": round(jaw, 1) if metrics_are_live else None,
            "shoulder_score": round(shoulders, 1) if metrics_are_live else None,
            "emotion": metrics.get("emotion_primary", "n/a") if metrics_are_live else "n/a",
            "emotion_confidence": metrics.get("emotion_confidence", 0) if metrics_are_live else 0,
            "face_quality": round(face_quality, 1) if metrics_are_live else 0,
        },
    }
    _last_valid_normalized_state = state
    return state


def api_state() -> dict:
    raw, from_live_file = read_overlay_state()
    return normalize_state(raw, from_live_file)


def demo_state() -> dict:
    state = fallback_state()
    state.update(
        {
            "mode": "demo",
            "is_live": False,
            "is_demo": True,
            "engine_connected": False,
            "camera_active": False,
            "message": "Demo mode: this is a product interface simulation.",
            "recommendation": "Risk is controlled. Keep jaw soft and shoulders low.",
            "alert_level": "normal",
        }
    )
    return state


def engine_health() -> dict:
    heartbeat = read_engine_heartbeat(DATA_DIR)
    heartbeat_age = heartbeat_age_seconds(DATA_DIR)
    raw, from_live_file = read_overlay_state()
    updated_at = parse_iso_timestamp(raw.get("updated_at")) if raw else None
    state_age = None
    if updated_at:
        state_age = max(
            0.0,
            (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds(),
        )
    return {
        "engine_connected": heartbeat_age is not None and heartbeat_age <= HEARTBEAT_MAX_AGE_SECONDS,
        "heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
        "heartbeat": heartbeat,
        "overlay_file_exists": OVERLAY_STATE_PATH.exists(),
        "overlay_file_valid": bool(raw),
        "overlay_from_live_file": from_live_file,
        "overlay_state_age_seconds": round(state_age, 1) if state_age is not None else None,
        "overlay_running": bool(raw.get("running")) if raw else False,
    }


def data_quality() -> dict:
    state = api_state()
    health = engine_health()
    llm = intel.llm_health(timeout=1.0)
    raw = state.get("raw") or {}
    performance = (read_overlay_state()[0] or {}).get("performance_profile") or {}
    quality = scoring.data_quality_score(
        engine_connected=bool(state.get("engine_connected")),
        camera_active=bool(state.get("camera_active")),
        signal_confidence=as_float(state.get("signal_confidence"), 0.0),
        face_visible=state.get("mode") == "live" and as_float(raw.get("face_quality"), 0.0) >= 60,
        shoulders_visible=state.get("mode") == "live" and bool((read_overlay_state()[0] or {}).get("metrics")),
        llm_connected=bool(llm.get("connected")),
        fps=as_float(performance.get("fps"), 0.0),
    )
    return {
        **quality,
        "state_mode": state.get("mode"),
        "engine": health,
        "llm": llm,
    }


def performance_profile() -> dict:
    raw, _ = read_overlay_state()
    raw = raw or {}
    profile = raw.get("performance_profile") or {}
    state = api_state()
    llm = intel.llm_health(timeout=1.0)
    fps = as_float(profile.get("fps"), 0.0)
    frame_ms = as_float(profile.get("frame_ms"), 0.0)
    pose_ms = as_float(profile.get("pose_ms"), 0.0)
    face_ms = as_float(profile.get("face_ms"), 0.0)
    ui_ms = max(0.0, frame_ms - pose_ms - face_ms)
    return {
        "status": "ready",
        "mode": state.get("mode"),
        "camera_active": bool(state.get("camera_active")),
        "engine_connected": bool(state.get("engine_connected")),
        "fps": round(fps, 1),
        "frame_ms": round(frame_ms, 1),
        "pose_ms": round(pose_ms, 1),
        "face_ms": round(face_ms, 1),
        "ui_ms": round(ui_ms, 1),
        "llm_latency_ms": llm.get("latency_ms"),
        "llm_status": llm.get("status"),
        "healthy_for_demo": bool(state.get("is_live")) and fps >= 8 and as_float(state.get("signal_confidence"), 0) >= 0.55,
        "recommendation": (
            "Ready for live demo."
            if bool(state.get("is_live")) and fps >= 8 and as_float(state.get("signal_confidence"), 0) >= 0.55
            else "For the investor demo, start a live session, improve lighting, and keep local fallback coach ready."
        ),
    }


def system_health() -> dict:
    state = api_state()
    llm = intel.llm_health(timeout=1.0)
    perf = performance_profile()
    return {
        "mode": state.get("mode"),
        "is_live": bool(state.get("is_live")),
        "numbers_visible": bool(state.get("numbers_visible")),
        "engine_connected": bool(state.get("engine_connected")),
        "camera_active": bool(state.get("camera_active")),
        "signal_confidence": as_float(state.get("signal_confidence"), 0.0),
        "message": state.get("message"),
        "llm": {
            "connected": bool(llm.get("connected")),
            "status": llm.get("status", "fallback"),
            "provider": llm.get("provider", "local"),
            "latency_ms": llm.get("latency_ms"),
        },
        "perf": {
            "fps": perf.get("fps"),
            "frame_ms": perf.get("frame_ms"),
            "pose_ms": perf.get("pose_ms"),
            "face_ms": perf.get("face_ms"),
            "ui_ms": perf.get("ui_ms"),
        },
    }


def demo_readiness() -> dict:
    quality = data_quality()
    perf = performance_profile()
    checks = [
        {"name": "Live engine", "ok": bool(perf.get("engine_connected")), "value": perf.get("mode")},
        {"name": "Camera/session", "ok": bool(perf.get("camera_active")), "value": perf.get("camera_active")},
        {"name": "Signal", "ok": as_float(quality.get("score"), 0) >= 55, "value": quality.get("score")},
        {"name": "FPS", "ok": as_float(perf.get("fps"), 0) >= 8, "value": perf.get("fps")},
        {"name": "LLM/fallback", "ok": True, "value": perf.get("llm_status")},
    ]
    ready = all(item["ok"] for item in checks[:4])
    return {
        "status": "ready" if ready else "needs_attention",
        "ready_for_live_demo": ready,
        "checks": checks,
        "message": "Live demo ready." if ready else "Use pitch/browser demo for now; live engine still needs attention.",
        "safe_fallback": "local coach command",
    }


def safe_static_path(request_path: str) -> Path | None:
    route = request_path.strip("/")
    route_map = {
        "": "index.html",
        "demo": "demo.html",
        "overlay": "overlay.html",
        "tester": "tester.html",
        "pitch": "pitch.html",
        "metrics": "metrics.html",
        "report": "report.html",
        "privacy": "privacy.html",
        "play": "play.html",
        "camera-check": "camera_check.html",
        "evidence": "evidence.html",
        "data-room": "data_room.html",
        "study": "study.html",
        "share": "share.html",
        "validation": "validation.html",
        "pitch-demo": "pitch_demo.html",
        "admin": "admin.html",
    }
    if route == "favicon.ico":
        # Keep browser noise down even when no custom favicon is shipped.
        route = "index.html"
    else:
        route = route_map.get(route, route)

    candidate = (SITE_ROOT / route).resolve()
    try:
        candidate.relative_to(SITE_ROOT.resolve())
    except ValueError:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if candidate.exists():
        return candidate

    # Defensive fallback for clean routes like "/play" => "play.html".
    if "." not in route and route:
        html_candidate = (SITE_ROOT / f"{route}.html").resolve()
        try:
            html_candidate.relative_to(SITE_ROOT.resolve())
        except ValueError:
            return None
        if html_candidate.exists():
            return html_candidate
    return None


def proof_share_payload() -> dict:
    """Returns the data needed to render a shareable proof card client-side.

    The actual PNG is generated in the browser (canvas API) so we don't
    ship raw frames to the server. We only return the proof numbers and
    a watermark string.
    """
    proof = intel.proof_card()
    return {
        "headline": proof.get("headline"),
        "tilt_before": proof.get("tilt_before"),
        "tilt_after": proof.get("tilt_after"),
        "tilt_delta": proof.get("tilt_delta"),
        "recovery_seconds": proof.get("recovery_seconds"),
        "command": proof.get("command"),
        "jaw": proof.get("jaw"),
        "shoulders": proof.get("shoulders"),
        "source": proof.get("source"),
        "is_synthetic": bool(proof.get("is_synthetic")),
        "watermark": "Kinaesthetic AI · localhost",
        "claim_disclaimer": "Performance coaching, not medical advice.",
    }


def feedback_stats() -> dict:
    """Aggregated label counts for the live tester dashboard."""
    feedback = intel.tail_jsonl(intel.FEEDBACK_PATH, 100_000)
    labels = {"felt_tension": 0, "helped": 0, "false_alert": 0}
    for row in feedback:
        payload = row.get("payload") or {}
        for key in labels:
            if payload.get(key):
                labels[key] += 1
    total = sum(labels.values())
    return {
        "total_alerts_with_label": total,
        "labels": labels,
        "help_rate_percent": round(labels["helped"] / total * 100, 1) if total else None,
        "false_alert_rate_percent": (
            round(labels["false_alert"] / total * 100, 1) if total else None
        ),
        "feedback_records": len(feedback),
    }


def admin_summary() -> dict:
    """Compact ops view for the /admin dashboard.

    Returns counts, last-record timestamps and CORS/auth posture so an
    operator can verify the deployment is healthy.
    """
    consent_path = DATA_DIR / "consent_log.jsonl"
    sessions = intel.tail_jsonl(intel.PUBLIC_SESSIONS_PATH, 100_000)
    feedback = intel.tail_jsonl(intel.FEEDBACK_PATH, 100_000)
    coach_mem = intel.tail_jsonl(intel.COACH_MEMORY_PATH, 100_000)
    consent_rows = intel.tail_jsonl(consent_path, 100_000) if consent_path.exists() else []

    def _last_ts(rows: list[dict]) -> str | None:
        for row in reversed(rows):
            ts = row.get("timestamp") or row.get("started_at") or row.get("accepted_at")
            if ts:
                return str(ts)
        return None

    return {
        "service": "kinaesthetic-site",
        "port": PORT,
        "engine": engine_health(),
        "data": {
            "sessions": {"count": len(sessions), "last_at": _last_ts(sessions)},
            "feedback": {"count": len(feedback), "last_at": _last_ts(feedback)},
            "coach_memory": {"count": len(coach_mem), "last_at": _last_ts(coach_mem)},
            "consent": {"count": len(consent_rows), "last_at": _last_ts(consent_rows)},
        },
        "feedback_stats": feedback_stats(),
        "security": {
            "auth_enabled": not security.auth_disabled(),
            "consent_version": security.CURRENT_CONSENT_VERSION,
            "allowed_origins": security.allowed_origins(),
            "rate_limit_write_per_minute": security.RATE_LIMIT_WRITE[0],
            "rate_limit_read_per_minute": security.RATE_LIMIT_READ[0],
        },
        "deprecated_routes": DEPRECATED_ROUTES,
    }


class KinaestheticSiteHandler(BaseHTTPRequestHandler):
    server_version = "KinaestheticAISite/0.2"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[site_server] {self.address_string()} - {fmt % args}")

    def _client_ip(self) -> str:
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if forwarded:
            return forwarded
        return self.address_string()

    def send_headers(
        self,
        status: int,
        content_type: str,
        cache: bool = False,
        extra: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        origin = self.headers.get("Origin")
        for header, value in security.collect_security_headers(origin):
            self.send_header(header, value)
        self.send_header("Cache-Control", "public, max-age=60" if cache else "no-store")
        if extra:
            for header, value in extra:
                self.send_header(header, value)
        self.end_headers()

    def send_json(
        self,
        payload: dict,
        status: int = 200,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_headers(status, "application/json; charset=utf-8", extra=extra_headers)
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def do_OPTIONS(self) -> None:
        self.send_headers(204, "text/plain; charset=utf-8")

    def _rate_limit_or_reject(self, *, write: bool) -> bool:
        ok, remaining, retry_after = security.rate_limit(self._client_ip(), write=write)
        if ok:
            return True
        self.send_json(
            {
                "ok": False,
                "error": "rate_limited",
                "retry_after_seconds": retry_after,
            },
            status=429,
            extra_headers=[("Retry-After", str(int(retry_after) or 1))],
        )
        return False

    def _check_write_security(self) -> bool:
        if not self._rate_limit_or_reject(write=True):
            return False
        # Origin: if a browser sends Origin header, reject when not in whitelist.
        origin = self.headers.get("Origin")
        if origin and not security.origin_allowed(origin):
            self.send_json({"ok": False, "error": "origin_not_allowed"}, status=403)
            return False
        ok_token, token_reason = security.check_write_token(self.headers)
        if not ok_token:
            self.send_json({"ok": False, "error": token_reason or "auth_required"}, status=401)
            return False
        ok_consent, consent_reason = security.check_consent(self.headers)
        if not ok_consent:
            self.send_json(
                {
                    "ok": False,
                    "error": consent_reason or "consent_required",
                    "current_consent_version": security.CURRENT_CONSENT_VERSION,
                },
                status=403,
            )
            return False
        return True

    def _deprecation_headers(self, path: str) -> list[tuple[str, str]]:
        successor = DEPRECATED_ROUTES.get(path)
        if not successor:
            return []
        return [
            ("Deprecation", "true"),
            ("Sunset", DEPRECATION_SUNSET),
            ("Link", f"<{successor}>; rel=\"successor-version\""),
            ("X-Kai-Successor", successor),
        ]

    def _inject_html_token(self, html_text: str) -> str:
        token = security.public_token_for_html()
        snippet = (
            "<script>window.__KAI_WRITE_TOKEN__="
            + json.dumps(token)
            + ";window.__KAI_CONSENT_VERSION__="
            + json.dumps(security.CURRENT_CONSENT_VERSION)
            + ";</script>"
        )
        idx = html_text.lower().find("</head>")
        if idx >= 0:
            return html_text[:idx] + snippet + html_text[idx:]
        return snippet + html_text

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/") and not self._rate_limit_or_reject(write=False):
            return

        if path == "/healthz" or path == "/api/healthz":
            self.send_json({"ok": True, "service": "kinaesthetic-site", "port": PORT})
            return

        if path == "/api/auth-status":
            self.send_json(
                {
                    "consent_version": security.CURRENT_CONSENT_VERSION,
                    "auth_enabled": not security.auth_disabled(),
                    "allowed_origins": security.allowed_origins(),
                }
            )
            return

        if path == "/api/admin-summary":
            self.send_json(admin_summary())
            return

        if path == "/api/session-stream":
            self._handle_sse()
            return

        if path == "/api/state":
            query = parse_qs(parsed.query)
            state = demo_state() if query.get("demo", ["0"])[0] == "1" else api_state()
            self.send_json(state)
            return

        if path == "/api/llm-health":
            self.send_json(intel.llm_health())
            return

        if path == "/api/engine-health":
            self.send_json(engine_health())
            return

        if path == "/api/data-quality":
            self.send_json(data_quality())
            return

        if path == "/api/performance-profile":
            self.send_json(performance_profile())
            return
        if path == "/api/system-health":
            self.send_json(system_health())
            return

        if path == "/api/demo-readiness":
            self.send_json(demo_readiness())
            return

        if path == "/api/report":
            self.send_json(intel.session_report())
            return

        if path == "/api/session-replay":
            self.send_json(intel.session_replay())
            return

        if path == "/api/proof-card":
            self.send_json(intel.proof_card(), extra_headers=self._deprecation_headers(path))
            return

        if path == "/api/privacy-passport":
            self.send_json(intel.privacy_passport())
            return

        if path == "/api/body-search":
            self.send_json({"matches": intel.similar_body_states()})
            return

        if path == "/api/command-effectiveness":
            self.send_json(intel.command_effectiveness())
            return

        if path == "/api/command-effectiveness-table":
            self.send_json(intel.command_effectiveness_table())
            return

        if path == "/api/coach-command-library":
            self.send_json(intel.coach_command_library())
            return

        if path == "/api/false-alert-review":
            self.send_json(intel.false_alert_review())
            return

        if path == "/api/personal-model-score":
            query = parse_qs(parsed.query)
            self.send_json(intel.personal_model_score(query.get("id", ["default"])[0]))
            return

        if path == "/api/session-score":
            query = parse_qs(parsed.query)
            self.send_json(intel.public_session_score(query.get("id", [None])[0]))
            return

        if path == "/api/coach-memory":
            query = parse_qs(parsed.query)
            self.send_json(intel.coach_memory(query.get("tester", [None])[0], query.get("game", [None])[0]))
            return

        if path == "/api/validation-mode":
            query = parse_qs(parsed.query)
            self.send_json(intel.validation_mode_status(query.get("id", [None])[0]))
            return

        if path == "/api/autopilot-v2":
            self.send_json(intel.autopilot_v2())
            return

        if path == "/api/investor-metrics":
            self.send_json(intel.investor_metrics())
            return

        if path == "/api/cohort-summary":
            self.send_json(intel.cohort_summary())
            return

        if path == "/api/data-room":
            self.send_json(intel.data_room())
            return

        if path == "/api/baseline-profile":
            query = parse_qs(parsed.query)
            self.send_json(intel.load_baseline_profile(query.get("id", ["default"])[0]))
            return

        if path == "/api/validation-study-summary":
            self.send_json(intel.validation_study_summary())
            return

        if path == "/api/feedback-stats":
            self.send_json(feedback_stats())
            return

        if path == "/api/proof-share":
            self.send_json(proof_share_payload())
            return

        if path == "/api/share-proof":
            query = parse_qs(parsed.query)
            self.send_json(intel.share_proof(query.get("id", [None])[0]))
            return

        if path == "/api/pitch-demo-script":
            self.send_json(intel.investor_demo_script(), extra_headers=self._deprecation_headers(path))
            return

        if path == "/api/export-founder-deck":
            self.send_json(intel.export_founder_deck_package())
            return

        if path == "/api/export-pitch-package":
            self.send_json(
                intel.export_founder_deck_package(),
                extra_headers=self._deprecation_headers(path),
            )
            return

        if path == "/api/record-pitch-run":
            self.send_json(intel.pitch_run_package())
            return

        if path == "/api/evidence":
            self.send_json(intel.evidence_summary())
            return

        if path == "/api/session":
            query = parse_qs(parsed.query)
            intel.expire_stale_public_session()
            self.send_json(intel.public_session_summary(query.get("id", [None])[0]))
            return

        if path == "/api/session-timeline":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["240"])[0] or 240)
            self.send_json(intel.public_session_timeline(query.get("id", [None])[0], limit=limit))
            return

        if path == "/api/storage-health":
            self.send_json(intel.hosted_storage_status())
            return

        if path == "/api/rollup":
            query = parse_qs(parsed.query)
            self.send_json(intel.rollup_summary(int(query.get("days", ["7"])[0] or 7)))
            return

        if path == "/api/autolearn-status":
            self.send_json(intel.autolearn_status())
            return

        static_path = safe_static_path(path)
        if static_path is None:
            self.send_headers(404, "text/plain; charset=utf-8")
            self.wfile.write(b"Not found")
            return

        content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
        if static_path.suffix == ".wasm":
            content = static_path.read_bytes()
            content_type = "application/wasm"
        elif static_path.suffix == ".html":
            html_text = static_path.read_text(encoding="utf-8-sig")
            html_text = self._inject_html_token(html_text)
            content = html_text.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif content_type.startswith("text/") or static_path.suffix in {".js", ".mjs", ".css", ".svg"}:
            content = static_path.read_text(encoding="utf-8-sig").encode("utf-8")
            if static_path.suffix in {".js", ".mjs"}:
                content_type = "application/javascript; charset=utf-8"
            elif static_path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            else:
                content_type = f"{content_type}; charset=utf-8"
        else:
            content = static_path.read_bytes()

        self.send_headers(200, content_type, cache=False)
        self.wfile.write(content)

    def _handle_sse(self) -> None:
        """Server-Sent Events stream of normalized live state.

        Why SSE and not WebSocket: the clients only need server -> browser
        push for the live state at ~1 Hz. SSE is plain HTTP, no extra
        server library, and survives behind Render/Cloudflare without
        config tweaks. We yield a fresh ``api_state()`` every second up to
        a max-duration cap, then close so the client can reconnect.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        origin = self.headers.get("Origin")
        for header, value in security.collect_security_headers(origin):
            self.send_header(header, value)
        self.end_headers()

        deadline = time.time() + 60.0  # close after 60 seconds; client reconnects
        try:
            while time.time() < deadline:
                state = api_state()
                payload = json.dumps(state, ensure_ascii=False)
                try:
                    self.wfile.write(f"event: state\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(1.0)
        except Exception as exc:  # pragma: no cover - defensive
            try:
                self.wfile.write(f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n".encode("utf-8"))
            except OSError:
                pass

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in WRITE_ENDPOINTS:
            self.send_json({"ok": False, "error": "not_found"}, status=404)
            return

        # /api/consent has its own auth handling: rate-limit and origin check
        # only, because the client posts it BEFORE the consent state is
        # accepted. The body itself logs proof of consent.
        if parsed.path == "/api/consent":
            if not self._rate_limit_or_reject(write=True):
                return
            origin = self.headers.get("Origin")
            if origin and not security.origin_allowed(origin):
                self.send_json({"ok": False, "error": "origin_not_allowed"}, status=403)
                return
        else:
            if not self._check_write_security():
                return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(raw) if raw else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self.send_json({"ok": False, "error": "invalid_json"}, status=400)
            return

        # Drop mojibake strings *before* anything is persisted. This is the
        # last line of defence; the client and server are already utf-8.
        payload = security.sanitize_payload(payload) if isinstance(payload, dict) else {}
        if not isinstance(payload, dict):
            payload = {}

        if parsed.path == "/api/consent":
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ip": self._client_ip(),
                "user_agent": (self.headers.get("User-Agent") or "")[:240],
                "lang": str(payload.get("lang") or "")[:8],
                "consent_version": str(payload.get("consent_version") or security.CURRENT_CONSENT_VERSION)[:16],
                "tester_id": str(payload.get("tester_id") or "anonymous")[:80],
                "accepted_at": payload.get("accepted_at"),
            }
            try:
                security.log_consent(record)
                self.send_json({"ok": True, "consent_version": security.CURRENT_CONSENT_VERSION})
            except OSError as exc:
                self.send_json({"ok": False, "error": f"persist_failed: {exc}"}, status=500)
            return

        if parsed.path == "/api/baseline-profile":
            self.send_json(intel.save_baseline_profile(payload))
            return
        if parsed.path == "/api/study-event":
            self.send_json(intel.append_study_event(payload))
            return
        if parsed.path == "/api/coach":
            self.send_json(intel.generate_coach_response(payload))
            return
        if parsed.path == "/api/signals":
            self.send_json(intel.append_public_signal(payload))
            return
        if parsed.path == "/api/session":
            self.send_json(intel.handle_public_session(payload))
            return
        if parsed.path == "/api/session-heartbeat":
            self.send_json(intel.public_session_heartbeat(payload))
            return
        if parsed.path == "/api/camera-check":
            self.send_json(intel.append_camera_check(payload))
            return
        if parsed.path == "/api/queue-flush":
            events = payload.get("events") if isinstance(payload.get("events"), list) else []
            queued = []
            for item in events[:500]:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or item.get("path") or "unknown")
                event_payload = item.get("payload") if isinstance(item.get("payload"), dict) else item
                queued.append(intel.append_write_queue(kind, event_payload, reason="client_replay"))
            replay = intel.flush_write_queue(limit=500)
            self.send_json({"ok": True, "queued": len(queued), "replay": replay})
            return
        if parsed.path == "/api/autolearn-run":
            self.send_json(intel.run_autolearn_once(force=bool(payload.get("force"))))
            return
        self.send_json(intel.append_feedback(payload))


def main() -> None:
    SITE_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), KinaestheticSiteHandler)
    scheme = "http"
    if SSL_CERT_FILE and SSL_KEY_FILE:
        cert_path = Path(SSL_CERT_FILE).expanduser()
        key_path = Path(SSL_KEY_FILE).expanduser()
        if not cert_path.exists() or not key_path.exists():
            raise FileNotFoundError(f"SSL cert/key not found: {cert_path} / {key_path}")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    base_url = f"{scheme}://{HOST}:{PORT}"
    print(f"Kinaesthetic AI pitch/overlay server: {base_url}")
    print(f"Home: {base_url}/")
    print(f"Demo: {base_url}/demo")
    print(f"Pitch: {base_url}/pitch")
    print(f"Report: {base_url}/report")
    print(f"Privacy: {base_url}/privacy")
    print(f"Overlay: {base_url}/overlay")
    print(f"Testers: {base_url}/tester")
    print(f"API state: {base_url}/api/state")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping pitch/overlay server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
