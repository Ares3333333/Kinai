from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib import error, request

import gemini_coach


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "llama-3.1-8b-instant"
API_ROOT = "https://api.groq.com/openai/v1"
HEALTH_CACHE_TTL_SECONDS = 30.0

_health_cache: dict[str, Any] | None = None
_health_cache_at = 0.0

COACH_SYSTEM_PROMPT = (
    "You are an esports performance coach with biomechanics expertise. "
    "The player is approaching tilt or losing readiness. "
    "Return exactly ONE short command in English, maximum 10 words. "
    "No markdown, no list, no explanation, no medical claims. "
    "Use only safe actions: soften jaw, drop shoulders, exhale, widen gaze, reset posture, return focus. "
    "Never mention holding breath, reducing breath, enduring pain, diagnosis, therapy, or treatment."
)

BLOCKED_COMMAND_FRAGMENTS = (
    "hold your breath",
    "stop breathing",
    "endure pain",
    "through pain",
    "diagnos",
    "therapy",
    "treatment",
)


def read_dotenv() -> dict[str, str]:
    env_path = APP_ROOT / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_value(name: str, fallback: str = "") -> str:
    return os.getenv(name, "").strip() or read_dotenv().get(name, fallback).strip()


def groq_api_key() -> str:
    return env_value("GROQ_API_KEY", "")


def groq_model() -> str:
    return env_value("GROQ_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def _chat_completion(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: float,
    temperature: float = 0.2,
    max_tokens: int = 48,
) -> tuple[str, int]:
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    started = time.perf_counter()
    last_error: Exception | None = None
    raw = ""
    for attempt in range(2):
        req = request.Request(
            f"{API_ROOT}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "KinaestheticAI/0.1",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            break
        except error.HTTPError:
            raise
        except (TimeoutError, error.URLError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.18)
                continue
            raise
    if not raw and last_error:
        raise last_error
    latency_ms = round((time.perf_counter() - started) * 1000)
    data = json.loads(raw)
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return str(text).strip(), latency_ms


def sanitize_command(command: str, payload: dict[str, Any] | None = None) -> str:
    text = (command or "").strip().strip('"').strip()
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", text, flags=re.MULTILINE)
    text = next((part.strip() for part in text.splitlines() if part.strip()), text)
    for separator in (";", " / ", " then ", " and then "):
        if separator in text.lower():
            text = text.split(separator, 1)[0].strip()
    parts = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip()]
    if parts:
        text = parts[0]
    text = text.strip(" -–—:;,.")
    lowered = text.lower()
    if not text or any(fragment in lowered for fragment in BLOCKED_COMMAND_FRAGMENTS):
        return gemini_coach.fallback_command(payload)
    words = text.split()
    if len(words) > 10:
        text = " ".join(words[:10]).rstrip(".,;:")
    return text


def health(timeout: float = 2.5) -> dict[str, Any]:
    global _health_cache, _health_cache_at
    now = time.time()
    if _health_cache and now - _health_cache_at <= HEALTH_CACHE_TTL_SECONDS:
        cached = dict(_health_cache)
        cached["cached"] = True
        return cached

    api_key = groq_api_key()
    model = groq_model()
    if not api_key:
        result = {
            "provider": "groq",
            "status": "fallback",
            "connected": False,
            "model": model,
            "latency_ms": None,
            "message": "GROQ_API_KEY is not set. Trying the next provider.",
        }
        _health_cache = result
        _health_cache_at = now
        return result

    started = time.perf_counter()
    try:
        text, latency_ms = _chat_completion(
            api_key=api_key,
            model=model,
            messages=[{"role": "user", "content": "Reply with only: OK"}],
            timeout=timeout,
            temperature=0.0,
            max_tokens=8,
        )
        ok = bool(text)
        result = {
            "provider": "groq",
            "status": "connected" if ok else "error",
            "connected": ok,
            "model": model,
            "latency_ms": latency_ms,
            "message": "Groq is responding." if ok else "Groq returned an empty response.",
        }
    except TimeoutError:
        result = {
            "provider": "groq",
            "status": "timeout",
            "connected": False,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": "Groq timed out.",
        }
    except error.HTTPError as exc:
        result = {
            "provider": "groq",
            "status": "error",
            "connected": False,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": f"Groq HTTP {exc.code}. Check the key, model and limits.",
        }
    except error.URLError as exc:
        result = {
            "provider": "groq",
            "status": "offline",
            "connected": False,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": f"Groq is unavailable: {exc.reason}",
        }
    except Exception as exc:
        result = {
            "provider": "groq",
            "status": "error",
            "connected": False,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": f"Groq error: {exc}",
        }
    _health_cache = result
    _health_cache_at = now
    return result


def generate_coach_command(payload: dict[str, Any], timeout: float = 6.0) -> dict[str, Any]:
    api_key = groq_api_key()
    model = groq_model()
    if not api_key:
        return {
            "ok": False,
            "provider": "groq",
            "model": model,
            "command": "",
            "latency_ms": 0,
            "fallback": True,
            "message": "GROQ_API_KEY is not set.",
        }

    prompt = "Player state JSON. Return only one short English command, max 10 words:\n"
    prompt += json.dumps(payload or {}, ensure_ascii=False)
    started = time.perf_counter()
    try:
        text, latency_ms = _chat_completion(
            api_key=api_key,
            model=model,
            messages=[
                {"role": "system", "content": COACH_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            timeout=timeout,
            temperature=0.2,
            max_tokens=48,
        )
        command = sanitize_command(text, payload)
        return {
            "ok": True,
            "provider": "groq",
            "model": model,
            "command": command,
            "latency_ms": latency_ms,
            "fallback": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "groq",
            "model": model,
            "command": "",
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "fallback": True,
            "message": f"Groq is unavailable: {exc}",
        }
