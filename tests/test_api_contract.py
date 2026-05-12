"""End-to-end integration tests for site_server.

Spins up the real ``ThreadingHTTPServer`` on a random port, drives it with
``urllib.request``, and verifies the full request/response flow including
authentication, consent, rate-limit, deprecation, and the
session -> signals -> score happy path.

These tests do NOT mock the data layer. They write to a clean data
directory so they can't pollute production artifacts.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Boot site_server against an isolated data directory on a free port."""
    test_data = tmp_path_factory.mktemp("kai_data")
    # Re-route every "data dir" before importing site_server so the module
    # picks up the test directory.
    os.environ["KAI_WRITE_AUTH"] = "off"  # default off; tests opt back in
    os.environ["KAI_ALLOWED_ORIGINS"] = "http://testhost"

    # Force product_intelligence and security to use the temp DATA_DIR.
    import importlib

    # Remove cached modules in case other tests imported them first.
    for name in [
        "site_server",
        "product_intelligence",
        "security",
        "engine_status",
    ]:
        sys.modules.pop(name, None)

    # Patch the DATA_DIR attribute by injecting an env var that those modules
    # consult. Easiest: monkey-patch their module-level constants after import.
    import product_intelligence as intel  # noqa: WPS433

    intel.DATA_DIR = test_data
    intel.OVERLAY_STATE_PATH = test_data / "overlay_state.json"
    intel.AFFECTIVE_DATASET_PATH = test_data / "affective_somatic_dataset.jsonl"
    intel.EMBEDDINGS_PATH = test_data / "body_state_embeddings.jsonl"
    intel.FEEDBACK_PATH = test_data / "tester_feedback.jsonl"
    intel.EXPORTS_DIR = test_data / "founder_exports"
    intel.PROFILES_DIR = test_data / "profiles"
    intel.VALIDATION_STUDY_PATH = test_data / "validation_study.jsonl"
    intel.PUBLIC_SIGNALS_PATH = test_data / "public_browser_signals.jsonl"
    intel.PUBLIC_SESSIONS_PATH = test_data / "public_sessions.jsonl"
    intel.PUBLIC_SESSION_STATE_PATH = test_data / "public_session_state.json"
    intel.COACH_MEMORY_PATH = test_data / "coach_memory.jsonl"

    import security  # noqa: WPS433

    security.DATA_DIR = test_data
    security.TOKEN_FILE = test_data / ".write_token"
    # reset cached token so tests start clean
    security._WRITE_TOKEN = None  # type: ignore[attr-defined]
    security._RATE_BUCKETS.clear()  # type: ignore[attr-defined]

    import site_server  # noqa: WPS433

    site_server.DATA_DIR = test_data
    site_server.OVERLAY_STATE_PATH = test_data / "overlay_state.json"

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    httpd = ThreadingHTTPServer(("127.0.0.1", port), site_server.KinaestheticSiteHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    # Wait briefly for socket to be ready.
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{base}/healthz", timeout=0.5).read()
            break
        except (urllib.error.URLError, socket.error):
            time.sleep(0.1)

    yield base, intel, security, httpd

    httpd.shutdown()
    thread.join(timeout=2)


def _request(url: str, *, method: str = "GET", body=None, headers=None, expect_status: int | None = None):
    data = None
    req_headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return resp.status, payload, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") or "{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body}
        if expect_status is not None and exc.code != expect_status:
            raise
        return exc.code, payload, dict(exc.headers or {})


def test_healthz(server):
    base, *_ = server
    status, payload, _ = _request(f"{base}/healthz")
    assert status == 200
    assert payload["ok"] is True


def test_state_offline_when_no_overlay(server):
    base, *_ = server
    status, payload, _ = _request(f"{base}/api/state")
    assert status == 200
    assert payload["mode"] in {"offline", "demo"}


def test_session_signals_score_happy_path(server):
    base, *_ = server
    # 1. Start a session via /api/session (auth disabled in this test).
    status, payload, _ = _request(
        f"{base}/api/session",
        method="POST",
        body={"action": "start", "source": "test", "tester_id": "qa", "game": "valorant"},
        headers={"X-Kai-Consent": "v1"},
    )
    assert status == 200, payload
    assert payload.get("ok") is True
    session_id = payload.get("session", {}).get("session_id")
    assert session_id

    # 2. Push three signals.
    for tilt in (30, 65, 88):
        _request(
            f"{base}/api/signals",
            method="POST",
            body={
                "session_id": session_id,
                "tester_id": "qa",
                "tilt_risk": tilt,
                "readiness": 100 - tilt,
                "recovery": 50,
                "jaw_tension": "high" if tilt > 60 else "low",
                "shoulder_tension": "medium",
                "signal_confidence": 0.8,
                "source": "test",
            },
            headers={"X-Kai-Consent": "v1"},
        )

    # 3. End the session.
    _request(
        f"{base}/api/session",
        method="POST",
        body={"action": "end", "session_id": session_id, "tester_id": "qa"},
        headers={"X-Kai-Consent": "v1"},
    )

    # 4. Score and timeline must include this session.
    status, score, _ = _request(f"{base}/api/session-score?id={session_id}")
    assert status == 200
    assert score.get("session_id") == session_id

    status, timeline, _ = _request(f"{base}/api/session-timeline?id={session_id}&limit=10")
    assert status == 200
    assert timeline.get("session_id") == session_id


def test_consent_log_persisted(server):
    base, _intel, security, _httpd = server
    status, payload, _ = _request(
        f"{base}/api/consent",
        method="POST",
        body={"lang": "en", "tester_id": "qa", "source": "integration_test"},
    )
    assert status == 200, payload
    assert payload.get("ok") is True
    consent_path = security.DATA_DIR / "consent_log.jsonl"
    assert consent_path.exists()
    rows = [json.loads(line) for line in consent_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(r.get("source") == "integration_test" or r.get("tester_id") == "qa" for r in rows)


def test_admin_summary_includes_security(server):
    base, *_ = server
    status, payload, _ = _request(f"{base}/api/admin-summary")
    assert status == 200
    assert "security" in payload
    assert "auth_enabled" in payload["security"]
    assert "allowed_origins" in payload["security"]
    assert "data" in payload


def test_deprecation_headers_on_legacy_routes(server):
    base, *_ = server
    status, _payload, headers = _request(f"{base}/api/proof-card")
    assert status == 200
    headers_lower = {k.lower(): v for k, v in headers.items()}
    assert headers_lower.get("deprecation") == "true"
    assert "successor-version" in headers_lower.get("link", "")


def test_cors_origin_whitelist(server):
    base, *_ = server
    # Allowed origin echoed back.
    status, _, headers = _request(
        f"{base}/api/state", headers={"Origin": "http://testhost"}
    )
    assert status == 200
    headers_lower = {k.lower(): v for k, v in headers.items()}
    assert headers_lower.get("access-control-allow-origin") == "http://testhost"
    # Not-allowed origin -> the header should not be set to the bad origin.
    status, _, headers2 = _request(
        f"{base}/api/state", headers={"Origin": "http://evil.example"}
    )
    assert status == 200
    headers2_lower = {k.lower(): v for k, v in headers2.items()}
    assert headers2_lower.get("access-control-allow-origin") != "http://evil.example"


def test_auth_required_when_enabled(server, monkeypatch):
    base, _intel, security, _httpd = server
    # Turn auth ON for this test.
    monkeypatch.setenv("KAI_WRITE_AUTH", "on")
    # Force-rebuild token to a known value.
    security._WRITE_TOKEN = None  # type: ignore[attr-defined]
    monkeypatch.setenv("KAI_WRITE_TOKEN", "secret-test-token")

    # No token -> 401.
    status, payload, _ = _request(
        f"{base}/api/feedback",
        method="POST",
        body={"helped": True},
        headers={"X-Kai-Consent": "v1"},
    )
    assert status == 401
    assert payload.get("error") in {"missing_token", "invalid_token", "auth_required"}

    # With token -> 200 (consent OK too).
    status, payload, _ = _request(
        f"{base}/api/feedback",
        method="POST",
        body={"helped": True, "source": "test"},
        headers={"X-Kai-Consent": "v1", "X-Kai-Token": "secret-test-token"},
    )
    assert status == 200, payload


def test_consent_required_when_auth_enabled(server, monkeypatch):
    base, _intel, security, _httpd = server
    monkeypatch.setenv("KAI_WRITE_AUTH", "on")
    security._WRITE_TOKEN = None  # type: ignore[attr-defined]
    monkeypatch.setenv("KAI_WRITE_TOKEN", "secret-test-token")

    status, payload, _ = _request(
        f"{base}/api/feedback",
        method="POST",
        body={"helped": True},
        headers={"X-Kai-Token": "secret-test-token"},
    )
    assert status == 403
    assert payload.get("error") in {"missing_consent", "stale_consent"}


def test_rate_limit_per_ip(server, monkeypatch):
    base, _intel, security, _httpd = server
    # Tighten the limits for the duration of this test.
    monkeypatch.setattr(security, "RATE_LIMIT_WRITE", (3, 60.0))
    security._RATE_BUCKETS.clear()  # type: ignore[attr-defined]
    # Force auth OFF for this test (other tests may have set it ON).
    monkeypatch.setenv("KAI_WRITE_AUTH", "off")
    monkeypatch.delenv("KAI_WRITE_TOKEN", raising=False)
    security._WRITE_TOKEN = None  # type: ignore[attr-defined]

    # First three POSTs should succeed.
    for _ in range(3):
        status, _, _ = _request(
            f"{base}/api/feedback",
            method="POST",
            body={"helped": True, "source": "rate-test"},
            headers={"X-Kai-Consent": "v1"},
        )
        assert status == 200

    # Fourth one trips the limiter.
    status, payload, headers = _request(
        f"{base}/api/feedback",
        method="POST",
        body={"helped": True, "source": "rate-test"},
        headers={"X-Kai-Consent": "v1"},
        expect_status=429,
    )
    assert status == 429
    assert payload.get("error") == "rate_limited"
    headers_lower = {k.lower(): v for k, v in headers.items()}
    assert "retry-after" in headers_lower
