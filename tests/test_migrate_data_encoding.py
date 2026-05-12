"""Tests for the mojibake migration script."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import migrate_data_encoding as m  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_drop_if_only_mojibake(tmp_path):
    f = tmp_path / "coach_memory.jsonl"
    write_jsonl(
        f,
        [
            {"command": "?????? ???????", "tilt_risk": 80, "schema": "coach_memory_v1"},
            {"command": "Сделай вдох", "tilt_risk": 70, "schema": "coach_memory_v1"},
        ],
    )
    summary = m.process_file(f, "drop_if_only_mojibake", apply=True)
    assert summary["dropped"] == 1
    assert summary["fixed"] == 0
    rows = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["command"] == "Сделай вдох"


def test_null_field_keeps_record_with_other_data(tmp_path):
    f = tmp_path / "tester_feedback.jsonl"
    write_jsonl(
        f,
        [
            {
                "schema": "tester_feedback_v1",
                "payload": {
                    "helped": True,
                    "last_signals": {"recommendation": "?????? ???????", "tilt_risk": 65},
                },
            }
        ],
    )
    summary = m.process_file(f, "null_field", apply=True)
    assert summary["dropped"] == 0
    assert summary["fixed"] == 1
    row = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
    assert row["payload"]["helped"] is True
    assert row["payload"]["last_signals"]["recommendation"] is None
    assert row["payload"]["last_signals"]["tilt_risk"] == 65
    assert row["_encoding_warning"] == "mojibake_fixed"


def test_dry_run_does_not_touch_file(tmp_path):
    f = tmp_path / "x.jsonl"
    original = [{"command": "?????? ???????"}]
    write_jsonl(f, original)
    before = f.read_text(encoding="utf-8")
    m.process_file(f, "drop_if_only_mojibake", apply=False)
    after = f.read_text(encoding="utf-8")
    assert before == after


def test_idempotent_on_clean_file(tmp_path):
    f = tmp_path / "clean.jsonl"
    rows = [
        {"command": "Сделай вдох", "tilt_risk": 60},
        {"command": "Опусти плечи", "tilt_risk": 70},
    ]
    write_jsonl(f, rows)
    summary = m.process_file(f, "drop_if_only_mojibake", apply=True)
    assert summary["dropped"] == 0
    assert summary["fixed"] == 0
    re_run = m.process_file(f, "drop_if_only_mojibake", apply=True)
    assert re_run["dropped"] == 0
    assert re_run["fixed"] == 0
