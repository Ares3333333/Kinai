from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def as_float(value: Any, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class TiltAccumulatorState:
    shoulder_seconds: float
    asymmetry_seconds: float
    jaw_seconds: float
    emotion_seconds: float
    tilt_score: float


@dataclass(frozen=True)
class TiltAccumulatorResult:
    shoulder_seconds: float
    asymmetry_seconds: float
    jaw_seconds: float
    emotion_seconds: float
    target_tilt: float
    tilt_score: float


def readiness_score(
    tilt: float,
    jaw: float = 0.0,
    shoulders: float = 0.0,
    recovery: float = 0.0,
) -> float:
    return clamp(
        100
        - as_float(tilt) * 0.55
        - as_float(jaw) * 0.14
        - as_float(shoulders) * 0.16
        + as_float(recovery) * 0.25
    )


def feature_readiness(features: dict[str, Any]) -> float:
    return readiness_score(
        tilt=as_float(features.get("tilt_score")),
        jaw=as_float(features.get("jaw_clench_score")),
        shoulders=as_float(features.get("shoulder_elevation_score")),
        recovery=as_float(features.get("recovery_score")),
    )


def biomechanical_raw_stress(
    shoulder_elevation: float,
    shoulder_asymmetry: float,
    jaw_clench: float,
    emotional_arousal: float,
) -> float:
    return clamp(
        as_float(shoulder_elevation) * 0.48
        + as_float(shoulder_asymmetry) * 0.34
        + as_float(jaw_clench) * 0.13
        + as_float(emotional_arousal) * 0.05
    )


def update_tilt_accumulator(
    *,
    shoulder_elevation: float,
    shoulder_asymmetry: float,
    jaw_clench: float,
    emotional_arousal: float,
    emotion_confidence: float,
    raw_stress: float,
    previous: TiltAccumulatorState,
    dt: float,
) -> TiltAccumulatorResult:
    dt = min(max(float(dt), 0.0), 0.5)

    shoulder_seconds = (
        min(previous.shoulder_seconds + dt, 3.2)
        if shoulder_elevation >= 45
        else max(previous.shoulder_seconds - dt * 0.45, 0.0)
    )
    asymmetry_seconds = (
        min(previous.asymmetry_seconds + dt, 2.0)
        if shoulder_asymmetry >= 30
        else max(previous.asymmetry_seconds - dt * 0.65, 0.0)
    )
    jaw_seconds = (
        min(previous.jaw_seconds + dt, 2.0)
        if jaw_clench >= 55
        else max(previous.jaw_seconds - dt * 0.75, 0.0)
    )
    # Emotion gate is intentionally easier to open than the body gates:
    # tester feedback showed tilt felt "dead" when arousal had to clear 62
    # for ≥1.4s before any contribution. We now accept arousal ≥ 42 with
    # confidence ≥ 30 and reach full gate after 0.7s, which matches user
    # perception ("when I look angry / wide-eyed / lips clenched, tilt
    # should move within a second").
    emotion_seconds = (
        min(previous.emotion_seconds + dt, 1.8)
        if emotional_arousal >= 42 and emotion_confidence >= 30
        else max(previous.emotion_seconds - dt * 0.55, 0.0)
    )

    # Posture gate accelerated from 3.0s → 1.6s. Forward head /
    # rolled shoulders shouldn't have to be sustained for three full
    # seconds before tilt notices — that felt "lazy" to testers.
    shoulder_gate = clamp(shoulder_seconds / 1.6, 0, 1)
    asymmetry_gate = clamp(asymmetry_seconds / 1.2, 0, 1)
    jaw_gate = clamp(jaw_seconds / 1.4, 0, 1)
    emotion_gate = clamp(emotion_seconds / 0.7, 0, 1)

    # Soft floor so even sub-threshold arousal nudges tilt — prevents the
    # "completely flat tilt during real frustration" regression. We start
    # the floor at arousal=22 so micro-frowns are not invisible.
    soft_emotion = clamp(max(0.0, emotional_arousal - 22.0) / 70.0, 0, 1)

    # Soft floor for posture mirrors the soft floor for emotion — even
    # below the main 45 gate, sub-threshold posture stress should nudge
    # tilt so micro-slouching is not invisible.
    soft_posture = clamp(max(0.0, shoulder_elevation - 18.0) / 70.0, 0, 1)

    target_tilt = clamp(
        shoulder_elevation * 0.70 * shoulder_gate
        + shoulder_elevation * 0.10 * soft_posture
        + shoulder_asymmetry * 0.28 * asymmetry_gate
        + jaw_clench * 0.17 * jaw_gate
        + emotional_arousal * 0.36 * emotion_gate
        + emotional_arousal * 0.18 * soft_emotion
        + raw_stress * 0.15
    )

    # Compound stress floors: real tilt is multi-modal (face + jaw +
    # shoulders + breath rising together). When several gates are open
    # at the same time we lift the floor so the bar reaches the upper
    # range it is actually meant to use. Tester complaint was "nothing
    # ever reaches the top, the bar feels capped".
    high_channels = sum(
        1 for fired in (
            shoulder_elevation >= 50 and shoulder_gate >= 0.3,
            shoulder_asymmetry >= 35 and asymmetry_gate >= 0.3,
            jaw_clench >= 55 and jaw_gate >= 0.3,
            emotional_arousal >= 50 and emotion_gate >= 0.3,
        ) if fired
    )
    if high_channels >= 4:
        target_tilt = max(target_tilt, 95.0)
    elif high_channels >= 3:
        target_tilt = max(target_tilt, 85.0)
    elif high_channels >= 2:
        target_tilt = max(target_tilt, 72.0)

    # Saturated extreme — face peaking together with shoulders or jaw is
    # screaming-tilt. Push to near-100 so the proof card actually shows
    # the worst moment honestly.
    if emotional_arousal >= 75 and emotion_gate >= 0.6 and (
        (shoulder_elevation >= 60 and shoulder_gate >= 0.6)
        or (jaw_clench >= 65 and jaw_gate >= 0.6)
    ):
        target_tilt = max(target_tilt, 97.0)

    target_tilt = clamp(target_tilt)

    response_speed = 1 - pow(2.718281828459045, -dt / 0.75)
    if target_tilt < previous.tilt_score:
        response_speed *= 0.42

    tilt_score = clamp(previous.tilt_score + (target_tilt - previous.tilt_score) * response_speed)
    return TiltAccumulatorResult(
        shoulder_seconds=shoulder_seconds,
        asymmetry_seconds=asymmetry_seconds,
        jaw_seconds=jaw_seconds,
        emotion_seconds=emotion_seconds,
        target_tilt=target_tilt,
        tilt_score=tilt_score,
    )


def confidence_gate(
    confidence: float,
    minimum: float = 0.55,
) -> bool:
    """True if the signal is trustworthy enough to surface numerical metrics.

    Used by the engine before writing to overlay_state and by site_server
    before exposing tilt/readiness numbers to UI. Below this threshold we
    show only setup hints, never numbers.
    """
    return as_float(confidence) >= float(minimum)


def recovery_delta(
    tilts: list[float],
    *,
    min_peak: float = 45.0,
) -> dict[str, Any]:
    """Honest before/after recovery summary.

    Returns ``status="empty"`` if there isn't a real peak. Never clamps
    the delta: investors and DD will divide our claims by the visible
    range, so a clamped 100 vs a real 38 is a credibility cliff.
    """
    if not tilts:
        return {"status": "empty", "delta": 0.0, "before": 0.0, "after": 0.0, "peak_index": None}
    cleaned = [as_float(t) for t in tilts]
    peak_index = max(range(len(cleaned)), key=lambda idx: cleaned[idx])
    peak = cleaned[peak_index]
    after = min(cleaned[peak_index:]) if peak_index < len(cleaned) else cleaned[-1]
    delta = max(peak - after, 0.0)
    if peak < min_peak:
        return {
            "status": "no_peak",
            "delta": round(delta, 1),
            "before": round(peak, 1),
            "after": round(after, 1),
            "peak_index": peak_index,
        }
    return {
        "status": "ready",
        "delta": round(delta, 1),
        "before": round(peak, 1),
        "after": round(after, 1),
        "peak_index": peak_index,
    }


def data_quality_score(
    *,
    engine_connected: bool,
    camera_active: bool,
    signal_confidence: float,
    face_visible: bool = False,
    shoulders_visible: bool = False,
    llm_connected: bool = False,
    fps: float = 0.0,
) -> dict[str, Any]:
    checks = [
        ("engine", bool(engine_connected), 18),
        ("camera", bool(camera_active), 18),
        ("signal", signal_confidence >= 0.65, 20),
        ("face", bool(face_visible), 14),
        ("shoulders", bool(shoulders_visible), 14),
        ("fps", as_float(fps) >= 8, 8),
        ("llm", bool(llm_connected), 8),
    ]
    score = sum(weight for _, ok, weight in checks if ok)
    failed = [name for name, ok, _ in checks if not ok]
    if score >= 82:
        label = "готово для live demo"
    elif score >= 58:
        label = "можно тестировать, но сигнал не идеален"
    else:
        label = "нужно улучшить setup"
    return {
        "score": round(clamp(score), 1),
        "label": label,
        "failed_checks": failed,
        "checks": {name: ok for name, ok, _ in checks},
    }
