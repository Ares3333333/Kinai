"""Tests for the alert decision layer.

Covers:
  - confidence floor blocks alerts
  - cooldown between alerts
  - duplicate command suppression
  - personal threshold rises with rolling tilt mean
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import alerts  # noqa: E402


def fresh_state() -> tuple[alerts.AlertState, alerts.AlertPolicy]:
    return alerts.AlertState(), alerts.AlertPolicy()


def test_low_confidence_blocks_alert():
    state, policy = fresh_state()
    decision = alerts.decide_alert(
        tilt=80,
        confidence=0.2,
        now=1000.0,
        state=state,
        policy=policy,
        fallback_threshold=50.0,
        recovery_active=False,
    )
    assert decision.should_alert is False
    assert decision.reason == "low_signal"


def test_below_threshold_blocks_alert():
    state, policy = fresh_state()
    decision = alerts.decide_alert(
        tilt=20,
        confidence=0.9,
        now=1000.0,
        state=state,
        policy=policy,
        fallback_threshold=55.0,
        recovery_active=False,
    )
    assert decision.should_alert is False
    assert decision.reason == "below_threshold"


def test_first_alert_fires_then_cooldown_blocks_second():
    state, policy = fresh_state()
    first = alerts.decide_alert(
        tilt=80,
        confidence=0.9,
        now=1000.0,
        state=state,
        policy=policy,
        fallback_threshold=50.0,
        recovery_active=False,
    )
    assert first.should_alert is True
    alerts.commit_alert(state=state, tilt=80, text="cmd", now=1000.0)

    second = alerts.decide_alert(
        tilt=82,
        confidence=0.9,
        now=1005.0,
        state=state,
        policy=policy,
        fallback_threshold=50.0,
        recovery_active=False,
    )
    assert second.should_alert is False
    assert second.reason == "cooldown"
    assert second.cooldown_remaining > 0


def test_cooldown_expires_eventually():
    state, policy = fresh_state()
    alerts.commit_alert(state=state, tilt=80, text="cmd", now=1000.0)
    later = alerts.decide_alert(
        tilt=80,
        confidence=0.9,
        now=1000.0 + policy.min_alert_gap_seconds + 5,
        state=state,
        policy=policy,
        fallback_threshold=50.0,
        recovery_active=False,
    )
    assert later.should_alert is True


def test_recovery_in_progress_blocks():
    state, policy = fresh_state()
    decision = alerts.decide_alert(
        tilt=80,
        confidence=0.9,
        now=1000.0,
        state=state,
        policy=policy,
        fallback_threshold=50.0,
        recovery_active=True,
    )
    assert decision.should_alert is False
    assert decision.reason == "recovery_in_progress"


def test_duplicate_command_suppressed_until_drop():
    state, policy = fresh_state()
    alerts.commit_alert(state=state, tilt=80, text="soft jaw", now=1000.0)
    decision = alerts.decide_alert(
        tilt=78,
        confidence=0.9,
        now=1000.0 + policy.min_alert_gap_seconds + 5,
        state=state,
        policy=policy,
        fallback_threshold=50.0,
        recovery_active=False,
        candidate_text="soft jaw",
    )
    assert decision.should_alert is False
    assert decision.reason == "duplicate_command"


def test_personal_threshold_reflects_rolling_window():
    state, policy = fresh_state()
    for _ in range(60):
        alerts.update_rolling(state, 70.0)
    threshold = alerts.personal_threshold(state, policy, fallback_threshold=50.0)
    assert threshold >= 70.0  # mean=70, std~0 -> threshold ~70
    assert threshold <= policy.max_threshold


def test_personal_threshold_falls_back_with_few_samples():
    state, policy = fresh_state()
    for _ in range(5):
        alerts.update_rolling(state, 70.0)
    assert alerts.personal_threshold(state, policy, fallback_threshold=42.0) == 42.0
