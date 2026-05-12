"""Tests for atomic file writes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import atomic_io  # noqa: E402


def test_atomic_write_text_creates_file(tmp_path: Path):
    target = tmp_path / "nested" / "out.txt"
    atomic_io.atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_json_pretty(tmp_path: Path):
    target = tmp_path / "data.json"
    atomic_io.atomic_write_json(target, {"a": 1, "b": [1, 2]})
    parsed = json.loads(target.read_text(encoding="utf-8"))
    assert parsed == {"a": 1, "b": [1, 2]}


def test_atomic_append_jsonl_appends(tmp_path: Path):
    target = tmp_path / "feed.jsonl"
    atomic_io.atomic_append_jsonl(target, {"id": 1})
    atomic_io.atomic_append_jsonl(target, {"id": 2})
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows == [{"id": 1}, {"id": 2}]


def test_safe_read_json_returns_none_on_missing(tmp_path: Path):
    assert atomic_io.safe_read_json(tmp_path / "no.json") is None


def test_safe_read_json_returns_none_on_corrupt(tmp_path: Path):
    target = tmp_path / "broken.json"
    target.write_text("not json", encoding="utf-8")
    assert atomic_io.safe_read_json(target) is None
