from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import product_intelligence as intel  # noqa: E402


def env_int(name: str, fallback: int) -> int:
    try:
        return int(os.getenv(name, str(fallback)))
    except ValueError:
        return fallback


def main() -> None:
    interval_min = max(1, env_int("AUTOLEARN_INTERVAL_MIN", 15))
    run_once = os.getenv("AUTOLEARN_ONCE", "0").strip().lower() in {"1", "true", "yes"}
    force = os.getenv("AUTOLEARN_FORCE", "0").strip().lower() in {"1", "true", "yes"}
    print(f"Kinaesthetic AI autolearn runner started. interval={interval_min}m force={force}")
    while True:
        result = intel.run_autolearn_once(force=force)
        print(
            "autolearn:",
            {
                "ok": result.get("ok"),
                "enabled": result.get("enabled"),
                "promoted": result.get("promoted"),
                "queue": (result.get("queue_flush") or {}).get("remaining"),
                "policy": (result.get("candidate") or {}).get("version"),
            },
        )
        if run_once:
            break
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
