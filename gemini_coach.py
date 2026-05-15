from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib import error, request


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "gemini-2.5-flash"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
HEALTH_CACHE_TTL_SECONDS = 30.0

COACH_SYSTEM_PROMPT = (
    "You are an esports performance coach with biomechanics expertise. "
    "Return exactly ONE short command in English, maximum 10 words. "
    "No markdown, no explanation, no medical claims. "
    "Use safe actions only: soften jaw, drop shoulders, exhale, widen gaze, reset posture, return focus."
)

LOCAL_FALLBACK_COMMANDS = [
    "Soften jaw. Drop shoulders. Long exhale.",
    "Release face. Sit tall. Widen your view.",
    "Shoulders lower. Eyes wide. One calm breath.",
    "Unclench jaw. Heavy elbows. Play the next moment.",
]

_health_cache: dict[str, Any] | None = None
_health_cache_at = 0.0


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


def gemini_model() -> str:
    return env_value("GEMINI_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def gemini_api_key() -> str:
    return env_value("GEMINI_API_KEY", "")


def fallback_command(payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    tilt = float(payload.get("tilt_risk") or payload.get("tilt_score") or 0)
    jaw = str(payload.get("jaw_tension") or payload.get("jaw") or "").lower()
    shoulder = str(payload.get("shoulder_tension") or payload.get("shoulders") or "").lower()
    if tilt >= 75 or jaw == "high":
        return LOCAL_FALLBACK_COMMANDS[0]
    if shoulder == "high":
        return LOCAL_FALLBACK_COMMANDS[2]
    return LOCAL_FALLBACK_COMMANDS[1]


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
    words = text.strip(" -–—:;,.").split()
    if not words:
        return fallback_command(payload)
    return " ".join(words[:10])


def _post_generate_content(
    *,
    api_key: str,
    model: str,
    contents: list[dict[str, Any]],
    timeout: float,
    temperature: float = 0.25,
    max_tokens: int = 48,
    system_instruction: bool = True,
) -> tuple[str, int]:
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": COACH_SYSTEM_PROMPT}]}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{API_ROOT}/models/{model}:generateContent",
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    started = time.perf_counter()
    with request.urlopen(req, timeout=timeout) as response:
        latency_ms = round((time.perf_counter() - started) * 1000)
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(str(part.get("text", "")) for part in parts).strip()
    return text, latency_ms


def health(timeout: float = 2.5) -> dict[str, Any]:
    global _health_cache, _health_cache_at
    now = time.time()
    if _health_cache and now - _health_cache_at <= HEALTH_CACHE_TTL_SECONDS:
        cached = dict(_health_cache)
        cached["cached"] = True
        return cached

    api_key = gemini_api_key()
    model = gemini_model()
    if not api_key:
        result = {
            "provider": "gemini",
            "status": "fallback",
            "connected": False,
            "model": model,
            "latency_ms": None,
            "message": "GEMINI_API_KEY is not set. Local command fallback is active.",
        }
        _health_cache = result
        _health_cache_at = now
        return result

    started = time.perf_counter()
    try:
        text, latency_ms = _post_generate_content(
            api_key=api_key,
            model=model,
            contents=[{"role": "user", "parts": [{"text": "Reply only: OK"}]}],
            timeout=timeout,
            temperature=0.0,
            max_tokens=16,
            system_instruction=False,
        )
        ok = bool(text)
        result = {
            "provider": "gemini",
            "status": "connected" if ok else "error",
            "connected": ok,
            "model": model,
            "latency_ms": latency_ms,
            "message": "Gemini is responding." if ok else "Gemini returned an empty response.",
        }
    except TimeoutError:
        result = {
            "provider": "gemini",
            "status": "timeout",
            "connected": False,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": "Gemini timed out. Local command fallback is active.",
        }
    except error.HTTPError as exc:
        result = {
            "provider": "gemini",
            "status": "error",
            "connected": False,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": f"Gemini HTTP {exc.code}. Check the key, model and quota.",
        }
    except error.URLError as exc:
        result = {
            "provider": "gemini",
            "status": "offline",
            "connected": False,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": f"Gemini is unavailable: {exc.reason}",
        }
    except Exception as exc:
        result = {
            "provider": "gemini",
            "status": "error",
            "connected": False,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": f"Gemini error: {exc}",
        }
    _health_cache = result
    _health_cache_at = now
    return result


def generate_coach_command(payload: dict[str, Any], timeout: float = 8.0) -> dict[str, Any]:
    api_key = gemini_api_key()
    model = gemini_model()
    if not api_key:
        return {
            "ok": True,
            "provider": "local_fallback",
            "model": "local_command_library",
            "command": fallback_command(payload),
            "latency_ms": 0,
            "fallback": True,
            "message": "GEMINI_API_KEY is not set.",
        }

    prompt = "Player state JSON. Return exactly one short English command:\n"
    prompt += json.dumps(payload or {}, ensure_ascii=False)
    started = time.perf_counter()
    try:
        text, latency_ms = _post_generate_content(
            api_key=api_key,
            model=model,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            timeout=timeout,
            temperature=0.25,
            max_tokens=48,
            system_instruction=True,
        )
        return {
            "ok": True,
            "provider": "gemini",
            "model": model,
            "command": sanitize_command(text, payload),
            "latency_ms": latency_ms,
            "fallback": False,
        }
    except Exception as exc:
        return {
            "ok": True,
            "provider": "local_fallback",
            "model": model,
            "command": fallback_command(payload),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "fallback": True,
            "message": f"Gemini unavailable, fallback active: {exc}",
        }
