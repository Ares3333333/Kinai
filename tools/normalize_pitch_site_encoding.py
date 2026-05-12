from __future__ import annotations

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "pitch_site"
    targets = list(root.glob("*.html")) + list(root.glob("*.js")) + list(root.glob("*.css"))
    changed = 0
    for path in targets:
        text = path.read_text(encoding="utf-8-sig")
        normalized = text.replace("\r\n", "\n")
        if normalized != text:
            changed += 1
        path.write_text(normalized, encoding="utf-8", newline="\n")
    print(f"normalized_files={len(targets)} changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
