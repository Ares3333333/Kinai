"""Compact TTS-friendly coach phrases.

The browser plays them via SpeechSynthesis (no audio files shipped). We
keep them short (<= 8 words) so they fit between two shots in a 200ms
gameplay gap.
"""

from __future__ import annotations

PHRASES_RU: dict[str, str] = {
    "jaw": "Челюсть мягко.",
    "shoulders": "Плечи вниз.",
    "breath": "Длинный выдох.",
    "reset": "Сброс. Плечи вниз. Выдох.",
    "focus": "Спина назад. Глаза мягче.",
    "default": "Мягкая челюсть. Плечи вниз. Выдох.",
}

PHRASES_EN: dict[str, str] = {
    "jaw": "Soft jaw.",
    "shoulders": "Drop shoulders.",
    "breath": "Long exhale.",
    "reset": "Reset. Drop shoulders. Exhale.",
    "focus": "Back tall. Soft eyes.",
    "default": "Soft jaw. Drop shoulders. Exhale.",
}


def pick_phrase(key: str, lang: str = "ru") -> str:
    pool = PHRASES_EN if lang.lower().startswith("en") else PHRASES_RU
    return pool.get(key, pool["default"])
