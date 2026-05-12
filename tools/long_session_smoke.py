from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from urllib.request import urlopen


def fetch_json(url: str) -> dict:
    with urlopen(url, timeout=3) as response:  # nosec - local smoke tool
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="30+ minute long-session smoke for site health.")
    parser.add_argument("--base", default="http://localhost:8502", help="Base URL for site server")
    parser.add_argument("--minutes", type=int, default=30, help="Duration in minutes")
    parser.add_argument("--interval", type=float, default=2.5, help="Polling interval seconds")
    args = parser.parse_args()

    started = time.time()
    duration = max(1, args.minutes) * 60
    errors = 0
    samples = 0
    print(f"[long-session] started {datetime.now().isoformat()} base={args.base} duration={args.minutes}m")

    while time.time() - started < duration:
        samples += 1
        try:
            state = fetch_json(f"{args.base}/api/state")
            health = fetch_json(f"{args.base}/api/system-health")
            llm = fetch_json(f"{args.base}/api/llm-health")
            mode = state.get("mode")
            conf = state.get("signal_confidence")
            fps = health.get("perf", {}).get("fps")
            print(f"[{samples:05d}] mode={mode} conf={conf} fps={fps} llm={llm.get('status')}")
        except Exception as exc:  # pragma: no cover - smoke utility
            errors += 1
            print(f"[{samples:05d}] ERROR: {exc}")
        time.sleep(max(0.5, args.interval))

    print(f"[long-session] finished samples={samples} errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
