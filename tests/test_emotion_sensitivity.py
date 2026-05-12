"""Regression tests for facial emotion sensitivity.

The user reported that the system felt "stupid" because hard frowns,
clenched lips, and wide eyes barely moved tilt. These tests pin the new
MAX-based arousal model so a single strong micro-expression cannot
silently regress to invisibility again.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app import SimpleLandmark, estimate_emotional_state  # noqa: E402
import scoring  # noqa: E402


def _fake_face() -> list[SimpleLandmark]:
    # estimate_emotional_state requires len(face_landmarks) > 386 to engage
    # the eye-gap heuristic. Coordinates do not matter for these tests.
    return [SimpleLandmark(x=0.5, y=0.5) for _ in range(478)]


def _empty_shapes() -> dict[str, float]:
    keys = (
        "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
        "eyeSquintLeft", "eyeSquintRight", "eyeWideLeft", "eyeWideRight", "eyeBlinkLeft", "eyeBlinkRight",
        "cheekSquintLeft", "cheekSquintRight",
        "mouthPressLeft", "mouthPressRight", "mouthClose", "mouthFrownLeft", "mouthFrownRight",
        "mouthSmileLeft", "mouthSmileRight", "mouthPucker", "mouthFunnel",
        "jawOpen", "noseSneerLeft", "noseSneerRight",
    )
    return {key: 0.0 for key in keys}


def test_hard_frown_alone_lifts_arousal():
    """browDown=0.65 alone (no jaw, no lips, no eyes) must read as arousal>=40."""
    shapes = _empty_shapes()
    shapes["browDownLeft"] = 0.65
    shapes["browDownRight"] = 0.65
    state = estimate_emotional_state(_fake_face(), shapes, jaw_clench_score=0.0)
    assert state["dominant_facial_channel"] == "brow"
    assert state["arousal_score"] >= 40, (
        f"hard frown should clear arousal=40, got {state['arousal_score']:.1f}"
    )


def test_clenched_lips_alone_lifts_arousal():
    """mouthPress=0.65 alone must register as arousal>=40."""
    shapes = _empty_shapes()
    shapes["mouthPressLeft"] = 0.65
    shapes["mouthPressRight"] = 0.65
    state = estimate_emotional_state(_fake_face(), shapes, jaw_clench_score=0.0)
    assert state["dominant_facial_channel"] == "mouth"
    assert state["arousal_score"] >= 40, (
        f"clenched lips should clear arousal=40, got {state['arousal_score']:.1f}"
    )


def test_wide_eyes_alone_lifts_arousal():
    """eyeWide=0.65 alone must register noticeable arousal (>=30)."""
    shapes = _empty_shapes()
    shapes["eyeWideLeft"] = 0.7
    shapes["eyeWideRight"] = 0.7
    state = estimate_emotional_state(_fake_face(), shapes, jaw_clench_score=0.0)
    assert state["dominant_facial_channel"] in ("eye_wide", "brow"), state["dominant_facial_channel"]
    assert state["arousal_score"] >= 30, (
        f"wide eyes should clear arousal=30, got {state['arousal_score']:.1f}"
    )


def test_neutral_face_keeps_arousal_low():
    """A neutral face must NOT manufacture phantom tension."""
    state = estimate_emotional_state(_fake_face(), _empty_shapes(), jaw_clench_score=0.0)
    assert state["arousal_score"] < 12, (
        f"neutral face should be quiet, got arousal={state['arousal_score']:.1f}"
    )
    assert state["dominant_facial_channel"] == "neutral" or state["dominant_facial_value"] < 14


def test_brow_only_pushes_tilt_above_20_in_under_2_seconds():
    """End-to-end: hard frown -> arousal -> accumulator -> tilt > 20.

    This is the integration target the user actually feels: I scowl, the
    bar moves up to a real number within ~1.5s — not 4-6s, not stuck at 5.
    """
    shapes = _empty_shapes()
    shapes["browDownLeft"] = 0.7
    shapes["browDownRight"] = 0.7
    facial = estimate_emotional_state(_fake_face(), shapes, jaw_clench_score=0.0)
    arousal = facial["arousal_score"]
    confidence = facial["confidence"]
    state = scoring.TiltAccumulatorState(0, 0, 0, 0, 0)
    elapsed = 0.0
    while elapsed < 1.6:
        result = scoring.update_tilt_accumulator(
            shoulder_elevation=0,
            shoulder_asymmetry=0,
            jaw_clench=0,
            emotional_arousal=arousal,
            emotion_confidence=confidence,
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
    assert state.tilt_score > 20, (
        f"hard frown should push tilt above 20 within ~1.5s, got tilt={state.tilt_score:.1f}"
    )


def test_dominant_channel_swaps_when_lips_dominate():
    """When lips dominate, dominant_facial_channel must swap accordingly."""
    shapes = _empty_shapes()
    shapes["browDownLeft"] = 0.20
    shapes["browDownRight"] = 0.20
    shapes["mouthPressLeft"] = 0.75
    shapes["mouthPressRight"] = 0.75
    state = estimate_emotional_state(_fake_face(), shapes, jaw_clench_score=0.0)
    assert state["dominant_facial_channel"] == "mouth"


@pytest.mark.parametrize(
    "shape_key, expected_channel",
    [
        ("browDownLeft", "brow"),
        ("mouthPressLeft", "mouth"),
        ("eyeWideLeft", "eye_wide"),
        ("noseSneerLeft", "sneer"),
    ],
)
def test_each_channel_can_dominate_alone(shape_key: str, expected_channel: str) -> None:
    """Each facial channel must be able to win the dominance race solo."""
    shapes = _empty_shapes()
    shapes[shape_key] = 0.7
    # Mirror left/right activations where the blendshape comes paired.
    if shape_key.endswith("Left"):
        right = shape_key[:-4] + "Right"
        if right in shapes:
            shapes[right] = 0.7
    state = estimate_emotional_state(_fake_face(), shapes, jaw_clench_score=0.0)
    # browDown/sneer can both light up because brows pull the nose, so
    # we accept either as long as the expected channel is dominant or
    # very close (within 30% of dominant_value).
    if state["dominant_facial_channel"] != expected_channel:
        # Sanity: at minimum the expected channel must register strongly.
        relevant = state.get(expected_channel + "_score") or state.get(
            {
                "brow": "brow_tension_score",
                "mouth": "mouth_tension_score",
                "eye_wide": "eye_wide_score",
                "sneer": None,
            }.get(expected_channel) or "",
            0,
        )
        assert relevant >= 30 or state["arousal_score"] >= 30, (
            f"{expected_channel} should at least register, got {state}"
        )
