"""Atomic file writers for JSON / JSONL / text artifacts.

Goal: no torn writes if the engine is killed mid-flush. Every consumer
(site_server, product_intelligence, beta exports) reads files that other
processes might be writing to in real-time. A torn write breaks JSON
parsing and silently drops data points. Use these helpers everywhere the
engine writes derived signals.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, payload: Any, indent: int | None = 2) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=indent),
    )


def atomic_append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON line atomically.

    Uses small lockfile-free strategy: read existing tail-safe, append,
    rewrite via temp+rename. For high-frequency workloads (>50 Hz) this is
    not optimal — but in this product we write at <2 Hz, so simplicity
    wins.

    We read with ``utf-8-sig`` to silently strip any BOM written by an
    earlier process (Windows Notepad / older Streamlit dumps). Without
    this, the BOM stays in the file forever and ``json.loads`` fails on
    the first line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8-sig")
        except OSError:
            existing = ""
    else:
        existing = ""
    atomic_write_text(path, existing + line)


def safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
