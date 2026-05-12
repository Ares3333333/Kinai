"""Unit tests for the security helper module."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import security  # noqa: E402


class FakeHeaders:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, name, default=None):
        for key, value in self._mapping.items():
            if key.lower() == name.lower():
                return value
        return default


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("KAI_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("KAI_WRITE_AUTH", raising=False)
    monkeypatch.delenv("KAI_ALLOWED_ORIGINS", raising=False)
    security._WRITE_TOKEN = None  # type: ignore[attr-defined]
    security._RATE_BUCKETS.clear()  # type: ignore[attr-defined]
    yield


def test_token_persists_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "TOKEN_FILE", tmp_path / ".write_token")
    monkeypatch.setattr(security, "DATA_DIR", tmp_path)
    security._WRITE_TOKEN = None  # type: ignore[attr-defined]
    a = security.write_token()
    b = security.write_token()
    assert a == b
    assert (tmp_path / ".write_token").read_text(encoding="utf-8").strip() == a


def test_check_write_token_accepts_bearer(monkeypatch, tmp_path):
    monkeypatch.setattr(security, "TOKEN_FILE", tmp_path / ".write_token")
    monkeypatch.setattr(security, "DATA_DIR", tmp_path)
    security._WRITE_TOKEN = None  # type: ignore[attr-defined]
    monkeypatch.setenv("KAI_WRITE_TOKEN", "abc")
    headers = FakeHeaders({"Authorization": "Bearer abc"})
    ok, reason = security.check_write_token(headers)
    assert ok and reason is None


def test_check_write_token_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(security, "TOKEN_FILE", tmp_path / ".write_token")
    monkeypatch.setattr(security, "DATA_DIR", tmp_path)
    security._WRITE_TOKEN = None  # type: ignore[attr-defined]
    monkeypatch.setenv("KAI_WRITE_TOKEN", "abc")
    headers = FakeHeaders({"X-Kai-Token": "wrong"})
    ok, reason = security.check_write_token(headers)
    assert not ok and reason == "invalid_token"


def test_check_consent_versioned():
    headers = FakeHeaders({})
    ok, reason = security.check_consent(headers)
    assert not ok and reason == "missing_consent"

    headers = FakeHeaders({"X-Kai-Consent": "v0"})
    ok, reason = security.check_consent(headers)
    assert not ok and reason == "stale_consent"

    headers = FakeHeaders({"X-Kai-Consent": "v1"})
    ok, _ = security.check_consent(headers)
    assert ok


def test_origin_whitelist(monkeypatch):
    monkeypatch.setenv("KAI_ALLOWED_ORIGINS", "https://kinaestheticai.com, https://app.test.com")
    assert security.origin_allowed("https://kinaestheticai.com")
    assert security.origin_allowed("https://app.test.com/")  # trailing slash tolerated
    assert not security.origin_allowed("https://evil.example")
    # No origin (curl, server-to-server) is fine.
    assert security.origin_allowed(None)


def test_rate_limit_blocks_after_quota(monkeypatch):
    monkeypatch.setattr(security, "RATE_LIMIT_WRITE", (2, 60.0))
    security._RATE_BUCKETS.clear()  # type: ignore[attr-defined]
    assert security.rate_limit("1.2.3.4", write=True)[0]
    assert security.rate_limit("1.2.3.4", write=True)[0]
    blocked, remaining, retry = security.rate_limit("1.2.3.4", write=True)
    assert not blocked
    assert remaining == 0
    assert retry > 0
    # Different IP not affected.
    assert security.rate_limit("9.9.9.9", write=True)[0]


def test_sanitize_payload_strips_mojibake():
    payload = {
        "command": "?????? ???????",
        "tilt": 84,
        "nested": {"recommendation": "????? ????"},
        "list": ["ok", "?????? ???????"],
    }
    cleaned = security.sanitize_payload(payload)
    assert cleaned["command"] is None
    assert cleaned["tilt"] == 84
    assert cleaned["nested"]["recommendation"] is None
    assert cleaned["list"][0] == "ok"
    assert cleaned["list"][1] is None


def test_is_mojibake_text_handles_real_cyrillic():
    assert security.is_mojibake_text("?????? ???????")
    assert not security.is_mojibake_text("Сделай вдох")
    assert not security.is_mojibake_text("hello?")  # one ? is normal punctuation
    assert not security.is_mojibake_text("")
