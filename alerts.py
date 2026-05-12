"""Alert decision layer.

Why a separate module: the original ``maybe_generate_coach_alert`` lives
inside the 5900-line monolith and entangles three things — *should we
alert*, *what to say*, *when to repeat*. Investors and testers will
immediately notice false alerts and alert spam, so we isolate the
decision logic, make it deterministic and testable, and wire it back
into ``app.py`` with a one-line call.

Three guarantees:
1. Adaptive personal threshold — baseline + Z * std, not a global 50/100.
2. Anti-spam cooldown — at least N seconds between alerts.
3. De-duplication — don't show the same command back-to-back.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class AlertPolicy:
    """Configurable thresholds. Defaults are tuned for first-time users.

    `min_alert_gap_seconds` - hard floor between alerts (anti-spam).
    `max_alert_gap_seconds` - if everything is calm, don't talk for >N sec.
    `recovery_required_drop` - tilt must drop this much before the same
        alert can fire again.
    `cooldown_after_recovery` - seconds of silence after a recovery.
    `personal_threshold_z`   - sigma above baseline that counts as tilt.
    `min_threshold`, `max_threshold` - clamp the personal threshold.
    """

    min_alert_gap_seconds: float = 30.0
    cooldown_after_recovery: float = 25.0
    recovery_required_drop: float = 12.0
    personal_threshold_z: float = 1.4
    min_threshold: float = 45.0
    max_threshold: float = 80.0
    confidence_floor: float = 0.55
    rolling_window: int = 240  # ~30s at ~8Hz


@dataclass
class AlertState:
    """Mutable state. Owned by the engine, passed in/out of decide()."""

    last_alert_at: float = 0.0
    last_alert_text: str = ""
    last_alert_tilt: float = 0.0
    last_recovered_at: float = 0.0
    rolling_tilts: Deque[float] = field(default_factory=lambda: deque(maxlen=240))


@dataclass(frozen=True)
class AlertDecision:
    should_alert: bool
    reason: str
    threshold_used: float
    cooldown_remaining: float = 0.0


def update_rolling(state: AlertState, tilt: float) -> None:
    if math.isfinite(tilt):
        state.rolling_tilts.append(float(tilt))


def personal_threshold(
    state: AlertState,
    policy: AlertPolicy,
    fallback_threshold: float,
) -> float:
    """Compute personal alert threshold from the rolling window.

    If we don't have enough samples yet, fall back to caller's value
    (typically the global default already used by the engine). This keeps
    behaviour identical for the first ~30 seconds of a session.
    """
    samples = list(state.rolling_tilts)
    if len(samples) < 30:
        return float(fallback_threshold)
    mean = sum(samples) / len(samples)
    variance = sum((x - mean) ** 2 for x in samples) / len(samples)
    std = math.sqrt(variance)
    raw = mean + policy.personal_threshold_z * std
    return max(policy.min_threshold, min(policy.max_threshold, raw))


def decide_alert(
    *,
    tilt: float,
    confidence: float,
    now: float | None,
    state: AlertState,
    policy: AlertPolicy,
    fallback_threshold: float,
    recovery_active: bool,
    candidate_text: str = "",
) -> AlertDecision:
    """Pure decision function. Does not mutate state on no-go paths.

    Mutate state only when ``should_alert`` is True (the caller sees the
    alert as committed). This makes unit testing trivial.
    """
    now = float(now if now is not None else time.time())
    threshold = personal_threshold(state, policy, fallback_threshold)

    if confidence < policy.confidence_floor:
        return AlertDecision(False, "low_signal", threshold)

    if recovery_active:
        return AlertDecision(False, "recovery_in_progress", threshold)

    if tilt < threshold:
        return AlertDecision(False, "below_threshold", threshold)

    cooldown_remaining = max(
        0.0,
        policy.min_alert_gap_seconds - (now - state.last_alert_at),
    )
    if cooldown_remaining > 0:
        return AlertDecision(False, "cooldown", threshold, cooldown_remaining)

    since_recovery = now - state.last_recovered_at if state.last_recovered_at else 1e9
    if since_recovery < policy.cooldown_after_recovery:
        return AlertDecision(
            False,
            "post_recovery_grace",
            threshold,
            policy.cooldown_after_recovery - since_recovery,
        )

    drop_since_last = state.last_alert_tilt - tilt
    same_text = bool(candidate_text) and candidate_text == state.last_alert_text
    if same_text and drop_since_last < policy.recovery_required_drop:
        return AlertDecision(False, "duplicate_command", threshold)

    return AlertDecision(True, "fire", threshold)


def commit_alert(
    *,
    state: AlertState,
    tilt: float,
    text: str,
    now: float | None = None,
) -> None:
    state.last_alert_at = float(now if now is not None else time.time())
    state.last_alert_tilt = float(tilt)
    state.last_alert_text = text


def commit_recovery(*, state: AlertState, now: float | None = None) -> None:
    state.last_recovered_at = float(now if now is not None else time.time())
