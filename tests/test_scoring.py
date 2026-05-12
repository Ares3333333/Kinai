"""Unit tests for scoring helpers.

These functions are pure, deterministic and run on every PR — they catch
silent regressions in the math that drives investor-visible numbers.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scoring  # noqa: E402


def test_clamp_bounds():
    assert scoring.clamp(-10) == 0
    assert scoring.clamp(150) == 100
    assert scoring.clamp(50) == 50


def test_readiness_drops_with_tilt():
    high_tilt = scoring.readiness_score(tilt=80, jaw=60, shoulders=70)
    low_tilt = scoring.readiness_score(tilt=10, jaw=10, shoulders=10)
    assert low_tilt > high_tilt


def test_recovery_delta_empty():
    result = scoring.recovery_delta([])
    assert result["status"] == "empty"
    assert result["delta"] == 0.0


def test_recovery_delta_real_peak():
    series = [10, 20, 80, 60, 40, 20]
    result = scoring.recovery_delta(series)
    assert result["status"] == "ready"
    assert math.isclose(result["before"], 80, abs_tol=0.01)
    assert math.isclose(result["after"], 20, abs_tol=0.01)
    assert math.isclose(result["delta"], 60, abs_tol=0.01)


def test_recovery_delta_no_peak_marks_status():
    series = [10, 12, 14, 13, 12]  # never crosses min_peak
    result = scoring.recovery_delta(series, min_peak=45.0)
    assert result["status"] == "no_peak"
    assert result["delta"] >= 0.0  # never negative


def test_recovery_delta_does_not_clamp_to_100():
    series = [5, 10, 95, 0]  # 95 -> 0 = 95 point delta, not 100
    result = scoring.recovery_delta(series)
    assert result["delta"] == 95.0


def test_confidence_gate():
    assert scoring.confidence_gate(0.7) is True
    assert scoring.confidence_gate(0.3) is False
    assert scoring.confidence_gate(0.55) is True


def test_data_quality_failed_checks():
    quality = scoring.data_quality_score(
        engine_connected=False,
        camera_active=False,
        signal_confidence=0.2,
        face_visible=False,
        shoulders_visible=False,
        llm_connected=False,
        fps=0.0,
    )
    assert quality["score"] == 0
    assert "engine" in quality["failed_checks"]


def test_emotion_lifts_tilt_after_short_window():
    """Regression: tilt must visibly respond to sustained anger/frustration.

    Previous version required arousal>=62 + confidence>=48 + 1.4s — too
    strict for human-feeling response. We accept arousal>=50 + conf>=35 and
    reach full gate at ~0.9s.
    """
    state = scoring.TiltAccumulatorState(
        shoulder_seconds=0,
        asymmetry_seconds=0,
        jaw_seconds=0,
        emotion_seconds=0,
        tilt_score=0,
    )
    # Simulate 1.0s of moderate-strong arousal with realistic confidence.
    for _ in range(10):
        result = scoring.update_tilt_accumulator(
            shoulder_elevation=0,
            shoulder_asymmetry=0,
            jaw_clench=0,
            emotional_arousal=58,
            emotion_confidence=50,
            raw_stress=0,
            previous=state,
            dt=0.1,
        )
        state = scoring.TiltAccumulatorState(
            shoulder_seconds=result.shoulder_seconds,
            asymmetry_seconds=result.asymmetry_seconds,
            jaw_seconds=result.jaw_seconds,
            emotion_seconds=result.emotion_seconds,
            tilt_score=result.tilt_score,
        )
    assert state.tilt_score > 5, f"tilt should rise from arousal, got {state.tilt_score}"


def test_emotion_soft_floor_when_below_main_gate():
    """Even sub-threshold arousal must give a small lift (no dead zone).

    The user reported that micro-frowns / quick lip clenches felt
    invisible. We start the soft floor at arousal=22 to fix that, while
    keeping the main 0.7s gate at arousal>=42.
    """
    state = scoring.TiltAccumulatorState(0, 0, 0, 0, 0)
    result = scoring.update_tilt_accumulator(
        shoulder_elevation=0,
        shoulder_asymmetry=0,
        jaw_clench=0,
        emotional_arousal=35,  # below 42 main gate
        emotion_confidence=40,
        raw_stress=0,
        previous=state,
        dt=0.2,
    )
    assert result.target_tilt > 0, "soft floor must contribute even below gate"


def _drive_accumulator(*, shoulder, asym, jaw, arousal, conf, raw_stress=0, ticks=20, dt=0.1):
    """Helper: run update_tilt_accumulator for `ticks` iterations and
    return the final TiltAccumulatorState."""
    state = scoring.TiltAccumulatorState(0, 0, 0, 0, 0)
    for _ in range(ticks):
        result = scoring.update_tilt_accumulator(
            shoulder_elevation=shoulder,
            shoulder_asymmetry=asym,
            jaw_clench=jaw,
            emotional_arousal=arousal,
            emotion_confidence=conf,
            raw_stress=raw_stress,
            previous=state,
            dt=dt,
        )
        state = scoring.TiltAccumulatorState(
            shoulder_seconds=result.shoulder_seconds,
            asymmetry_seconds=result.asymmetry_seconds,
            jaw_seconds=result.jaw_seconds,
            emotion_seconds=result.emotion_seconds,
            tilt_score=result.tilt_score,
        )
    return state


def test_compound_two_channels_lifts_tilt_above_60():
    """Two channels co-firing must push tilt into the upper-mid range.

    Floor for 2 channels is 70 — even with smoothing the score should
    settle above 60 in 2 seconds.
    """
    state = _drive_accumulator(shoulder=60, asym=0, jaw=0, arousal=58, conf=55, ticks=20)
    assert state.tilt_score > 60, f"2-channel compound should clear 60, got {state.tilt_score:.1f}"


def test_compound_three_channels_lifts_tilt_above_75():
    """Three channels co-firing must push tilt into the high zone."""
    state = _drive_accumulator(shoulder=60, asym=0, jaw=60, arousal=60, conf=55, ticks=25)
    assert state.tilt_score > 75, f"3-channel compound should clear 75, got {state.tilt_score:.1f}"


def test_compound_four_channels_saturates_high():
    """All four channels firing must saturate near maximum."""
    state = _drive_accumulator(shoulder=70, asym=50, jaw=70, arousal=70, conf=60, ticks=30)
    assert state.tilt_score > 85, f"4-channel compound should clear 85, got {state.tilt_score:.1f}"


def test_saturated_extreme_pushes_above_90():
    """Genuine screaming-tilt (face + shoulders both peak) → tilt > 90."""
    state = _drive_accumulator(shoulder=80, asym=60, jaw=80, arousal=80, conf=70, ticks=30)
    assert state.tilt_score > 90, f"saturated extreme should clear 90, got {state.tilt_score:.1f}"


def test_calm_face_keeps_tilt_low():
    """Calm body + calm face must still read calm — no phantom 70s."""
    state = _drive_accumulator(shoulder=10, asym=5, jaw=10, arousal=15, conf=40, ticks=20)
    assert state.tilt_score < 25, f"calm should stay low, got {state.tilt_score:.1f}"


def test_data_quality_full_house():
    quality = scoring.data_quality_score(
        engine_connected=True,
        camera_active=True,
        signal_confidence=0.9,
        face_visible=True,
        shoulders_visible=True,
        llm_connected=True,
        fps=15.0,
    )
    assert quality["score"] == 100
    assert quality["failed_checks"] == []
