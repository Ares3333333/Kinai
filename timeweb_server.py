from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


APP_ROOT = Path(__file__).resolve().parent
SITE_ROOT = APP_ROOT / "pitch_site"
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

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


class TimewebHandler(BaseHTTPRequestHandler):
    server_version = "KinaestheticTimeweb/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(self), microphone=()")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        self._send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send_bytes(200, path.read_bytes(), content_type)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in {"/healthz", "/api/healthz"}:
            self._send_json({"ok": True, "service": "kinaesthetic-timeweb", "port": PORT})
            return

        if path == "/api/state":
            self._send_json(DEMO_STATE)
            return

        if path in {"/api/system-health", "/api/engine-health", "/api/demo-readiness"}:
            self._send_json({"ok": True, "mode": "public_demo", "service": "kinaesthetic-timeweb"})
            return

        if path in {"/api/evidence", "/api/report", "/api/investor-metrics", "/api/cohort-summary"}:
            self._send_json(
                {
                    "ok": True,
                    "mode": "public_demo",
                    "summary": "Public hosted demo is online. Live camera analysis runs locally in the browser.",
                    "privacy": "Raw video is not uploaded or stored.",
                }
            )
            return

        if path == "/api/export-founder-deck":
            self._send_json({"ok": True, "mode": "public_demo", "files": []})
            return

        route_file = ROUTES.get(path)
        if route_file:
            self._send_file(SITE_ROOT / route_file)
            return

        candidate = (SITE_ROOT / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(SITE_ROOT.resolve())
        except ValueError:
            self._send_json({"ok": False, "error": "invalid_path"}, 400)
            return
        self._send_file(candidate)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length:
            _ = self.rfile.read(length)
        self._send_json(
            {
                "ok": True,
                "mode": "public_demo",
                "stored": False,
                "message": "Public demo accepted the event locally. Persistent storage is disabled in the fallback server.",
            }
        )


def main() -> None:
    SITE_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"Starting Kinaesthetic AI Timeweb server on {HOST}:{PORT}", flush=True)
    print(f"Health: http://{HOST}:{PORT}/healthz", flush=True)
    ThreadingHTTPServer((HOST, PORT), TimewebHandler).serve_forever()


if __name__ == "__main__":
    main()
