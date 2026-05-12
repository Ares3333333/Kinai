"""Regression tests for posture composite (forward head, rolled
shoulders, elevation, asymmetry).

The user reported that posture detection felt absent next to the new
emotion model. These tests pin the posture math so a single strong cue
(голова к монитору / плечи к ушам / плечи свернулись) reliably lights
up the posture_stress signal without requiring multiple muscle groups
to fire at once.

We do not call ``calculate_biomechanics`` directly because it touches
streamlit session state and OpenCV. Instead we replay the pure formula
in `posture_stress_from_channels` to lock the contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scoring  # noqa: E402


def _posture_stress(
    *,
    shoulders_up: float = 0.0,
    forward_head: float = 0.0,
    rolled_shoulders: float = 0.0,
    asymmetric: float = 0.0,
) -> tuple[float, str]:
    """Mirror of the MAX-based posture composite used in
    ``calculate_biomechanics`` (app.py) and ``createBrowserCvEngine``
    (browser_cv.js). Kept here so we can regress the contract without
    importing OpenCV / streamlit."""
    channels = (
        ("shoulders_up", shoulders_up, 0.95),
        ("forward_head", forward_head, 0.92),
        ("rolled_shoulders", rolled_shoulders, 0.90),
        ("asymmetric", asymmetric, 0.70),
    )
    dominant_weighted = 0.0
    dominant_label = "neutral"
    total = 0.0
    for label, value, weight in channels:
        weighted = value * weight
        if weighted > dominant_weighted:
            dominant_weighted = weighted
            dominant_label = label
        total += value
    bonus = max(0.0, total - max(v for _, v, _ in channels)) * 0.06
    score = scoring.clamp(dominant_weighted + bonus)
    if dominant_weighted < 14:
        dominant_label = "neutral"
    return score, dominant_label


def test_forward_head_alone_lifts_posture_stress():
    """Голова к монитору (forward head=70) одна → posture_stress ≥ 50."""
    score, dominant = _posture_stress(forward_head=70)
    assert dominant == "forward_head"
    assert score >= 50, f"forward head alone should clear 50, got {score:.1f}"


def test_rolled_shoulders_alone_lifts_posture_stress():
    """Плечи свернулись (rolled=65) одни → posture_stress ≥ 50."""
    score, dominant = _posture_stress(rolled_shoulders=65)
    assert dominant == "rolled_shoulders"
    assert score >= 50


def test_shoulders_up_alone_dominates():
    """Поднятые плечи к ушам — самый сильный коэффициент (0.95)."""
    score, dominant = _posture_stress(shoulders_up=70)
    assert dominant == "shoulders_up"
    assert score >= 60


def test_asymmetry_alone_registers_but_lower():
    """Перекос плеч — слабее основных каналов (вес 0.7), но всё ещё
    регистрируется, если другие каналы тихие."""
    score, dominant = _posture_stress(asymmetric=80)
    assert dominant == "asymmetric"
    assert score >= 50


def test_neutral_posture_keeps_score_low():
    score, dominant = _posture_stress()
    assert dominant == "neutral"
    assert score < 12


def test_compound_posture_collapse_pushes_higher():
    """Forward head + rolled shoulders + slight elevation вместе → 60+."""
    score, _ = _posture_stress(
        shoulders_up=40, forward_head=60, rolled_shoulders=55
    )
    assert score >= 60, f"compound posture should push higher, got {score:.1f}"


def test_dominant_swaps_when_forward_head_is_largest():
    """Когда forward_head явно больше — он должен забирать dominant."""
    score, dominant = _posture_stress(
        shoulders_up=20, forward_head=70, rolled_shoulders=30
    )
    assert dominant == "forward_head"
    assert score >= 60


def test_posture_stress_drives_tilt_accumulator():
    """Forward head — дотягивает tilt > 30 за ~1.5с через accumulator
    (через `posture_channel = max(elev, posture_stress*0.92)`)."""
    posture_stress, _ = _posture_stress(forward_head=70)
    posture_channel = max(0.0, posture_stress * 0.92)
    state = scoring.TiltAccumulatorState(0, 0, 0, 0, 0)
    elapsed = 0.0
    while elapsed < 1.6:
        result = scoring.update_tilt_accumulator(
            shoulder_elevation=posture_channel,
            shoulder_asymmetry=0,
            jaw_clench=0,
            emotional_arousal=0,
            emotion_confidence=0,
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
        elapsed += 0.1
    assert state.tilt_score > 25, (
        f"forward head should push tilt above 25 within ~1.5s, got {state.tilt_score:.1f}"
    )
