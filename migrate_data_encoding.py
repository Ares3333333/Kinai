"""One-shot migration: clean mojibake from append-only data files.

Symptom we saw in production data:
    {"command": "?????? ???????. ????? ????.", ...}
That string was a Russian coach phrase whose Cyrillic bytes got replaced
with ``?`` by a Windows pipe writing in cp1252 ``errors='replace'`` mode.

The original Cyrillic is unrecoverable (the bytes are gone), so the
correct treatment is:

* For records where the *only* meaningful text field is mojibake -> drop.
* For records that have other useful signal (numeric scores, booleans,
  session ids) -> keep but null out the mojibake field and add
  ``"_encoding_warning": "fixed"`` so we know which rows were salvaged.

Files migrated:
    data/coach_memory.jsonl       — coach memory derived from feedback
    data/tester_feedback.jsonl    — raw tester feedback
    data/public_browser_signals.jsonl  — derived signals stream
    data/public_sessions.jsonl    — public session start/stop log

Idempotent: running the script twice produces the same output.
Backup: writes ``<file>.pre-migration-<ts>.bak`` next to the original
*before* rewriting (kept locally; ``.gitignore``'d).

Run:
    python migrate_data_encoding.py            # dry-run report
    python migrate_data_encoding.py --apply    # rewrite files in place
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from force_utf8 import force_utf8


APP_ROOT = Path(__file__).resolve().parent
DATA_DIR = APP_ROOT / "data"

# Fields that are expected to carry human-readable Russian/English text.
TEXT_FIELDS = {
    "command",
    "recommendation",
    "coach_alert",
    "message",
    "comment",
    "note",
    "feedback_text",
    "label",
}

# Files we know about. Each entry: (path, behaviour)
# behaviour="drop_if_only_mojibake" — if the record's whole reason to exist is
#   the broken text field, drop the row; otherwise null out the bad field.
TARGETS: list[tuple[Path, str]] = [
    (DATA_DIR / "coach_memory.jsonl", "drop_if_only_mojibake"),
    (DATA_DIR / "tester_feedback.jsonl", "null_field"),
    (DATA_DIR / "public_browser_signals.jsonl", "null_field"),
    (DATA_DIR / "public_sessions.jsonl", "null_field"),
]


_MOJIBAKE_RE = re.compile(r"\?{3,}")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def is_mojibake(value: Any) -> bool:
    """Heuristic: text where >=40% of non-space chars are ``?`` and there is
    no Cyrillic letter at all is almost certainly mojibake.
    """
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return False
    if not _MOJIBAKE_RE.search(s):
        return False
    if _CYRILLIC_RE.search(s):
        return False
    no_space = s.replace(" ", "")
    if not no_space:
        return False
    ratio = no_space.count("?") / len(no_space)
    return ratio >= 0.4


def walk_strings(record: dict[str, Any]) -> list[tuple[list[str], str]]:
    """Return list of (path, value) for every string in the record."""
    found: list[tuple[list[str], str]] = []

    def _walk(node: Any, path: list[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, path + [str(i)])
        elif isinstance(node, str):
            found.append((path, node))

    _walk(record, [])
    return found


def patch_path(record: dict[str, Any], path: list[str], new_value: Any) -> None:
    cur: Any = record
    for key in path[:-1]:
        if isinstance(cur, list):
            cur = cur[int(key)]
        else:
            cur = cur[key]
    last = path[-1]
    if isinstance(cur, list):
        cur[int(last)] = new_value
    else:
        cur[last] = new_value


def is_text_field_path(path: list[str]) -> bool:
    return any(part in TEXT_FIELDS for part in path)


def clean_record(record: dict[str, Any], behaviour: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Returns (cleaned_record_or_None, list_of_path_strings_fixed)."""
    fixed_paths: list[str] = []
    bad_text_field = False
    for path, value in walk_strings(record):
        if not is_mojibake(value):
            continue
        path_str = ".".join(path)
        fixed_paths.append(path_str)
        if is_text_field_path(path):
            bad_text_field = True
        patch_path(record, path, None)

    if not fixed_paths:
        return record, []

    record["_encoding_warning"] = "mojibake_fixed"
    record["_encoding_warning_paths"] = fixed_paths

    if behaviour == "drop_if_only_mojibake" and bad_text_field:
        return None, fixed_paths
    return record, fixed_paths


def process_file(path: Path, behaviour: str, apply: bool) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "skipped": "missing"}

    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = raw.splitlines()
    out_lines: list[str] = []
    dropped = 0
    fixed = 0
    parse_errors = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        cleaned, paths = clean_record(record, behaviour)
        if not paths:
            out_lines.append(json.dumps(record, ensure_ascii=False))
            continue
        if cleaned is None:
            dropped += 1
            continue
        fixed += 1
        out_lines.append(json.dumps(cleaned, ensure_ascii=False))

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(APP_ROOT))
        except ValueError:
            return str(p)

    summary = {
        "path": _rel(path),
        "lines_in": len([line for line in lines if line.strip()]),
        "lines_out": len(out_lines),
        "fixed": fixed,
        "dropped": dropped,
        "parse_errors": parse_errors,
    }

    if apply and (fixed or dropped or parse_errors):
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_suffix(path.suffix + f".pre-migration-{ts}.bak")
        shutil.copy2(path, backup)
        path.write_text(("\n".join(out_lines) + "\n") if out_lines else "", encoding="utf-8")
        summary["backup"] = _rel(backup)
    elif not apply and (fixed or dropped):
        summary["dry_run"] = True
    return summary


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: dry-run report)")
    args = parser.parse_args(argv)

    print(f"[migrate_data_encoding] data dir: {DATA_DIR}")
    print(f"[migrate_data_encoding] mode: {'APPLY' if args.apply else 'DRY RUN'}")
    overall_fixed = 0
    overall_dropped = 0
    for path, behaviour in TARGETS:
        summary = process_file(path, behaviour, args.apply)
        overall_fixed += int(summary.get("fixed", 0))
        overall_dropped += int(summary.get("dropped", 0))
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"[migrate_data_encoding] total fixed={overall_fixed} dropped={overall_dropped}")
    if not args.apply and (overall_fixed or overall_dropped):
        print("[migrate_data_encoding] re-run with --apply to write changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
