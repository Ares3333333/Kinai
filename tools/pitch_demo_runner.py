"""Drive the live engine through a deterministic 90-second pitch demo.

Why this exists:
    The pitch deck shows a "before/after" recovery. To record it
    consistently we need the engine to walk through baseline -> rising
    tilt -> coach alert -> recovery -> proof in a known time budget.
    Reproducible takes are also useful for screenshot regression in the
    Playwright smoke suite.

How it works:
    For 90 seconds we:
      1. Start a public session via POST /api/session
      2. Push synthetic POST /api/signals records every 1s with a tilt
         curve that follows the canonical pitch timeline
         (baseline -> rising -> alert -> recovery -> proof)
      3. End the session and dump /api/share-proof to a local JSON file
         under data/pitch_runs/<timestamp>.json so a reviewer or screen
         recorder can attach evidence to the take.

The pitch site (/play, /share, /admin) renders directly from these
records so the same data feeds both the live demo and the founder deck.

Usage:
    python tools/pitch_demo_runner.py
    python tools/pitch_demo_runner.py --base http://localhost:8502 --duration 90 --speed 1.0
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from force_utf8 import force_utf8  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
PITCH_RUNS_DIR = DATA_DIR / "pitch_runs"

# Canonical 5-phase script that the deck is wired to.
# Each phase: (start_seconds, label, target_tilt, jaw, shoulders, command)
PHASES = [
    (0.0, "baseline", 22.0, "low", "low", "Бейзлайн зафиксирован."),
    (12.0, "rising", 56.0, "medium", "medium", "Риск растёт. Смягчи челюсть."),
    (28.0, "alert", 84.0, "high", "high", "Челюсть мягко. Плечи вниз. Выдох."),
    (54.0, "recovery", 48.0, "medium", "low", "Держи мягкость. Не зажимай дыхание."),
    (72.0, "proof", 32.0, "low", "low", "Восстановление доказано."),
]


def _phase_for(elapsed: float) -> tuple[str, float, str, str, str]:
    cur = PHASES[0]
    nxt = None
    for i, phase in enumerate(PHASES):
        if elapsed >= phase[0]:
            cur = phase
            nxt = PHASES[i + 1] if i + 1 < len(PHASES) else None
        else:
            break
    label, target_tilt, jaw, shoulders, command = cur[1], cur[2], cur[3], cur[4], cur[5]
    if nxt is None:
        smoothed = target_tilt
    else:
        # Linear interpolation between current phase target and next phase
        # target so the tilt curve doesn't have step-function jumps.
        span = max(0.001, nxt[0] - cur[0])
        ratio = max(0.0, min(1.0, (elapsed - cur[0]) / span))
        smoothed = target_tilt + (nxt[2] - target_tilt) * (ratio ** 1.4)
    # Add a small sinus jitter so the curve looks like a real signal.
    jitter = 1.5 * math.sin(elapsed * 1.7)
    return label, max(0.0, min(100.0, smoothed + jitter)), jaw, shoulders, command


def _post(base: str, path: str, body: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "X-Kai-Consent": "v1"}
    if token:
        headers["X-Kai-Token"] = token
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") or "{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body}
        return {"ok": False, "status": exc.code, "error": payload}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc)}


def _get(base: str, path: str) -> dict:
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=4.0) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc)}


def _pretty(label: str, payload: dict) -> str:
    head = f"[pitch_demo] {label}"
    if payload.get("ok") is False:
        return f"{head} FAILED: {payload}"
    return head


def run(base: str, duration: float, speed: float, token: str | None) -> Path:
    PITCH_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[pitch_demo] target server: {base} duration={duration}s speed={speed}x")

    started = _post(
        base,
        "/api/session",
        {
            "action": "start",
            "source": "pitch_demo_runner",
            "tester_id": "pitch_demo",
            "game": "valorant",
            "page_url": "tools/pitch_demo_runner.py",
        },
        token=token,
    )
    print(_pretty("session start", started))
    if not started.get("ok"):
        raise SystemExit(1)
    session_id = (started.get("session") or {}).get("session_id") or f"pitch-{int(time.time())}"

    samples: list[dict] = []
    start_wall = time.time()
    for tick in range(int(duration)):
        elapsed = tick * speed
        label, tilt, jaw, shoulders, command = _phase_for(elapsed)
        readiness = max(15.0, 100.0 - tilt * 0.85)
        recovery = max(10.0, 80.0 - abs(tilt - 50.0))
        signal = {
            "session_id": session_id,
            "tester_id": "pitch_demo",
            "game": "valorant",
            "source": "pitch_demo_runner",
            "tilt_risk": round(tilt, 1),
            "readiness": round(readiness, 1),
            "recovery": round(recovery, 1),
            "jaw_tension": jaw,
            "shoulder_tension": shoulders,
            "signal_confidence": 0.94,
            "fps": 30,
            "latency_ms": 18,
            "raw_landmarks": 511,
            "derived_points": 2048,
            "phase": label,
            "recommendation": command,
        }
        result = _post(base, "/api/signals", signal, token=token)
        ok = bool(result.get("ok"))
        samples.append({"elapsed": elapsed, "phase": label, "tilt": tilt, "command": command, "ok": ok})
        if tick % 5 == 0:
            print(f"[pitch_demo] t={elapsed:5.1f}s phase={label:<8} tilt={tilt:5.1f} ok={ok}")
        # Wait until next 1-second tick (real time, regardless of speed).
        next_at = start_wall + (tick + 1)
        time.sleep(max(0.0, next_at - time.time()))

    ended = _post(
        base,
        "/api/session",
        {"action": "end", "session_id": session_id, "tester_id": "pitch_demo", "source": "pitch_demo_runner"},
        token=token,
    )
    print(_pretty("session end", ended))
    proof = _get(base, f"/api/share-proof?id={session_id}")
    score = _get(base, f"/api/session-score?id={session_id}")
    timeline = _get(base, f"/api/session-timeline?id={session_id}&limit={int(duration)}")

    out_path = PITCH_RUNS_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{session_id}.json"
    out_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "duration_seconds": duration,
                "speed": speed,
                "phases": [{"start": p[0], "label": p[1], "target_tilt": p[2]} for p in PHASES],
                "samples": samples,
                "proof": proof,
                "score": score,
                "timeline": timeline,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[pitch_demo] saved {out_path}")
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8502", help="site_server base URL")
    parser.add_argument("--duration", type=int, default=90, help="run length in seconds")
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="phase progression speed multiplier (1.0 = real-time pitch)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="X-Kai-Token; if omitted, read from data/.write_token (auto-generated)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    args = parse_args(argv)
    token = args.token
    if token is None:
        token_file = REPO_ROOT / "data" / ".write_token"
        if token_file.exists():
            token = token_file.read_text(encoding="utf-8").strip() or None
    run(args.base, args.duration, args.speed, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
