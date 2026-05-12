from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENGINE_HEARTBEAT_FILENAME = "engine_heartbeat.json"
_last_valid_heartbeat: dict[str, Any] | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_engine_heartbeat(data_dir: Path, payload: dict[str, Any]) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / ENGINE_HEARTBEAT_FILENAME
    heartbeat = {
        "updated_at": utc_now_iso(),
        "engine": "streamlit_cockpit",
        **payload,
    }
    path.write_text(json.dumps(heartbeat, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_engine_heartbeat(data_dir: Path) -> dict[str, Any] | None:
    global _last_valid_heartbeat
    path = data_dir / ENGINE_HEARTBEAT_FILENAME
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _last_valid_heartbeat
    if isinstance(raw, dict):
        _last_valid_heartbeat = raw
        return raw
    return _last_valid_heartbeat


def heartbeat_age_seconds(data_dir: Path) -> float | None:
    path = data_dir / ENGINE_HEARTBEAT_FILENAME
    if not path.exists():
        return None
    try:
        return max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
    except OSError:
        return None
