from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser


BASE = "http://localhost:8502"
REQUIRED_IDS = {
    "startProductDemo",
    "stopProductDemo",
    "tiltValue",
    "readinessValue",
    "recoveryValue",
    "commandText",
    "proofHeadline",
    "runtimeMode",
}


class IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs):
        for key, value in attrs:
            if key == "id" and value:
                self.ids.add(value)


def fetch_text(path: str) -> str:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=6) as response:
        return response.read().decode("utf-8", errors="ignore")


def fetch_json(path: str) -> dict:
    return json.loads(fetch_text(path))


def main() -> int:
    issues: list[str] = []
    try:
        html = fetch_text("/play")
    except Exception as exc:  # pragma: no cover
        print(f"FAIL: /play is unreachable: {exc}")
        return 2

    parser = IdCollector()
    parser.feed(html)
    missing = sorted(REQUIRED_IDS - parser.ids)
    if missing:
        issues.append(f"missing DOM ids: {', '.join(missing)}")

    try:
        state = fetch_json("/api/state")
        mode = str(state.get("mode") or "unknown")
        if mode not in {"live", "offline", "demo", "stale"}:
            issues.append(f"/api/state mode is unexpected: {mode}")
    except Exception as exc:  # pragma: no cover
        issues.append(f"/api/state failed: {exc}")

    try:
        _ = fetch_json("/api/rollup")
    except Exception as exc:  # pragma: no cover
        issues.append(f"/api/rollup failed: {exc}")

    try:
        _ = fetch_json("/api/autolearn-status")
    except Exception as exc:  # pragma: no cover
        issues.append(f"/api/autolearn-status failed: {exc}")

    bad_token = "\u0432\u0402"
    if bad_token in html:
        issues.append("encoding artifact found in /play html")

    if issues:
        print("FAIL")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print("OK: /play smoke passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as exc:  # pragma: no cover
        print(f"FAIL: network error: {exc}")
        raise SystemExit(2)
