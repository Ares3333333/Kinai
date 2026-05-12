"""Security utilities for site_server: write-token auth, CORS whitelist,
per-IP rate limit, consent gate.

Design constraints (intentionally simple, NOT a substitute for real auth):

1. We don't have user accounts yet. The product is local-first; the only
   "secret" we can rely on is a shared deployment token. That's enough to
   stop random scrapers from spraying ``POST /api/feedback``.

2. The token is generated automatically the first time the server starts
   and persisted to ``data/.write_token``. It is also exposed to the
   *same-origin* HTML pages via ``window.__KAI_WRITE_TOKEN__`` so the JS
   on /play and /tester can sign its own POSTs without the user typing
   anything. External callers must pass ``X-Kai-Token`` or ``Authorization:
   Bearer <token>``.

3. CORS: an allow-list comes from ``KAI_ALLOWED_ORIGINS`` env (comma-
   separated). If a request has an ``Origin`` header that's NOT in the
   list, we don't echo it back. Same-origin browser requests don't send
   ``Origin`` so they "just work".

4. Per-IP rate limit: in-memory sliding window. Process-local; good
   enough for a single-instance Render deployment, must be replaced by
   Redis once we scale out.

5. Consent gate: the client sends ``X-Kai-Consent: v1`` (set after the
   user accepts the privacy notice on /play). If the header is missing
   on a *write* endpoint we return 403 + a JSON body that tells the
   frontend to re-show the consent prompt.
"""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Iterable

APP_ROOT = Path(__file__).resolve().parent
DATA_DIR = APP_ROOT / "data"
TOKEN_FILE = DATA_DIR / ".write_token"

CURRENT_CONSENT_VERSION = "v1"
RATE_LIMIT_WRITE = (60, 60.0)  # 60 writes per 60 sec per IP
RATE_LIMIT_READ = (600, 60.0)  # 600 reads per 60 sec per IP


_WRITE_TOKEN: str | None = None
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_RATE_LOCK = Lock()


def _split_csv(value: str) -> list[str]:
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


def allowed_origins() -> list[str]:
    raw = os.getenv("KAI_ALLOWED_ORIGINS", "").strip()
    if raw:
        return _split_csv(raw)
    # Sensible default for dev and the canonical hosts.
    return [
        "http://localhost:8502",
        "http://127.0.0.1:8502",
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "https://kinaestheticai.com",
        "https://www.kinaestheticai.com",
    ]


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True  # Same-origin / curl / non-browser
    return origin.rstrip("/") in allowed_origins()


def _read_or_create_token() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    env_token = os.getenv("KAI_WRITE_TOKEN", "").strip()
    if env_token:
        return env_token
    if TOKEN_FILE.exists():
        try:
            existing = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
    fresh = secrets.token_urlsafe(32)
    try:
        TOKEN_FILE.write_text(fresh, encoding="utf-8")
        # Best-effort lockdown on POSIX; on Windows os.chmod is mostly cosmetic.
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return fresh


def write_token() -> str:
    global _WRITE_TOKEN
    if _WRITE_TOKEN is None:
        _WRITE_TOKEN = _read_or_create_token()
    return _WRITE_TOKEN


def auth_disabled() -> bool:
    return os.getenv("KAI_WRITE_AUTH", "").strip().lower() in {"off", "disabled", "false", "0"}


def check_write_token(headers) -> tuple[bool, str | None]:
    """Return (ok, reason)."""
    if auth_disabled():
        return True, None
    expected = write_token()
    raw = headers.get("X-Kai-Token") or ""
    if not raw:
        bearer = headers.get("Authorization") or ""
        if bearer.lower().startswith("bearer "):
            raw = bearer.split(" ", 1)[1].strip()
    if not raw:
        return False, "missing_token"
    if not secrets.compare_digest(raw.strip(), expected):
        return False, "invalid_token"
    return True, None


def check_consent(headers) -> tuple[bool, str | None]:
    raw = (headers.get("X-Kai-Consent") or "").strip().lower()
    if not raw:
        return False, "missing_consent"
    if raw != CURRENT_CONSENT_VERSION:
        return False, "stale_consent"
    return True, None


def _prune(bucket: deque[float], now: float, window_seconds: float) -> None:
    horizon = now - window_seconds
    while bucket and bucket[0] < horizon:
        bucket.popleft()


def rate_limit(ip: str, *, write: bool) -> tuple[bool, int, float]:
    """Return (allowed, remaining, retry_after_seconds)."""
    if not ip:
        ip = "unknown"
    limit, window = RATE_LIMIT_WRITE if write else RATE_LIMIT_READ
    key = f"{ip}|{'w' if write else 'r'}"
    now = time.time()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS[key]
        _prune(bucket, now, window)
        if len(bucket) >= limit:
            retry = max(0.0, window - (now - bucket[0]))
            return False, 0, round(retry, 1)
        bucket.append(now)
        remaining = limit - len(bucket)
    return True, remaining, 0.0


def log_consent(record: dict) -> None:
    """Append a consent record to ``data/consent_log.jsonl``."""
    import atomic_io  # local import to avoid circulars at module load

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_io.atomic_append_jsonl(DATA_DIR / "consent_log.jsonl", record)


def is_mojibake_text(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if "?" not in value:
        return False
    no_space = value.replace(" ", "")
    if not no_space:
        return False
    has_cyr = any("\u0400" <= ch <= "\u04ff" for ch in value)
    if has_cyr:
        return False
    if no_space.count("?") < 3:
        return False
    return no_space.count("?") / len(no_space) >= 0.4


def sanitize_payload(node):
    """Recursively replace mojibake strings with ``None`` so corrupt data
    never lands in the data lake again. Returns the sanitized structure."""
    if isinstance(node, dict):
        return {k: sanitize_payload(v) for k, v in node.items()}
    if isinstance(node, list):
        return [sanitize_payload(v) for v in node]
    if isinstance(node, str) and is_mojibake_text(node):
        return None
    return node


def public_token_for_html() -> str:
    """Token snippet to embed into served HTML so same-origin JS can sign
    POST requests without the user logging in."""
    if auth_disabled():
        return ""
    return write_token()


def collect_security_headers(origin: str | None) -> Iterable[tuple[str, str]]:
    """Yields the (header, value) pairs needed for a CORS/security
    response. The caller is responsible for calling end_headers()."""
    if origin and origin_allowed(origin):
        yield "Access-Control-Allow-Origin", origin
        yield "Vary", "Origin"
        yield "Access-Control-Allow-Credentials", "true"
    yield "Access-Control-Allow-Methods", "GET, POST, OPTIONS"
    yield "Access-Control-Allow-Headers", "Content-Type, X-Kai-Token, X-Kai-Consent, Authorization"
    yield "Access-Control-Max-Age", "600"
    yield "X-Content-Type-Options", "nosniff"
    yield "Referrer-Policy", "strict-origin-when-cross-origin"


__all__ = [
    "CURRENT_CONSENT_VERSION",
    "allowed_origins",
    "origin_allowed",
    "write_token",
    "auth_disabled",
    "check_write_token",
    "check_consent",
    "rate_limit",
    "log_consent",
    "sanitize_payload",
    "is_mojibake_text",
    "public_token_for_html",
    "collect_security_headers",
]
