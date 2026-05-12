from __future__ import annotations

from typing import Any

import gemini_coach
import groq_coach


def health(timeout: float = 2.5) -> dict[str, Any]:
    """Return the health of the preferred cloud coach provider.

    Provider order is intentionally cost/speed first:
    Groq 8B instant -> Gemini -> local fallback.
    """
    groq = groq_coach.health(timeout=timeout)
    if groq.get("connected"):
        return {**groq, "primary_provider": "groq", "fallback_provider": "gemini"}

    gemini = gemini_coach.health(timeout=timeout)
    if gemini.get("connected"):
        return {
            **gemini,
            "primary_provider": "gemini",
            "fallback_provider": "local_fallback",
            "previous_provider": groq,
        }

    return {
        "provider": "local_fallback",
        "primary_provider": "local_fallback",
        "fallback_provider": None,
        "status": "fallback",
        "connected": False,
        "model": "local_command_library",
        "latency_ms": 0,
        "message": "Cloud coach недоступен. Используем локальные команды.",
        "previous_provider": groq,
        "secondary_provider": gemini,
    }


def generate_coach_command(payload: dict[str, Any], timeout: float = 8.0) -> dict[str, Any]:
    """Generate a coach command with provider failover."""
    groq = groq_coach.generate_coach_command(payload or {}, timeout=min(timeout, 6.0))
    if groq.get("ok") and groq.get("command") and not groq.get("fallback"):
        return {**groq, "provider_chain": ["groq"]}

    gemini = gemini_coach.generate_coach_command(payload or {}, timeout=timeout)
    if gemini.get("ok") and gemini.get("command") and not gemini.get("fallback"):
        return {
            **gemini,
            "provider_chain": ["groq", "gemini"],
            "previous_provider": groq,
        }

    return {
        "ok": True,
        "provider": "local_fallback",
        "model": "local_command_library",
        "command": gemini_coach.fallback_command(payload),
        "latency_ms": max(
            int(groq.get("latency_ms") or 0),
            int(gemini.get("latency_ms") or 0),
        ),
        "fallback": True,
        "provider_chain": ["groq", "gemini", "local_fallback"],
        "previous_provider": groq,
        "secondary_provider": gemini,
        "message": "Cloud providers недоступны, сработала локальная команда.",
    }
