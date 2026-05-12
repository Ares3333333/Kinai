from __future__ import annotations

import csv
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
import atomic_io
import coach_providers
import scoring


APP_ROOT = Path(__file__).resolve().parent
DATA_DIR = APP_ROOT / "data"
MODELS_DIR = APP_ROOT / "models"
OVERLAY_STATE_PATH = DATA_DIR / "overlay_state.json"
AFFECTIVE_DATASET_PATH = DATA_DIR / "affective_somatic_dataset.jsonl"
EMBEDDINGS_PATH = DATA_DIR / "body_state_embeddings.jsonl"
FEEDBACK_PATH = DATA_DIR / "tester_feedback.jsonl"
EXPORTS_DIR = DATA_DIR / "founder_exports"
PROFILES_DIR = DATA_DIR / "profiles"
VALIDATION_STUDY_PATH = DATA_DIR / "validation_study.jsonl"
PUBLIC_SIGNALS_PATH = DATA_DIR / "public_browser_signals.jsonl"
PUBLIC_SESSIONS_PATH = DATA_DIR / "public_sessions.jsonl"
PUBLIC_SESSION_STATE_PATH = DATA_DIR / "public_session_state.json"
COACH_MEMORY_PATH = DATA_DIR / "coach_memory.jsonl"
WRITE_QUEUE_PATH = DATA_DIR / "write_queue.jsonl"
CAMERA_CHECKS_PATH = DATA_DIR / "camera_checks.jsonl"
ROLLUPS_DIR = DATA_DIR / "rollups"
AUTOLEARN_STATE_PATH = DATA_DIR / "autolearn_state.json"
ACTIVE_POLICY_PATH = MODELS_DIR / "active_policy.json"
POLICY_LINEAGE_PATH = MODELS_DIR / "policy_lineage.jsonl"
SESSION_TIMEOUT_SECONDS = float(os.getenv("PUBLIC_SESSION_TIMEOUT_SECONDS", "18"))
VALIDATION_TESTER_TARGET = int(os.getenv("VALIDATION_TESTER_TARGET", "100"))
VALIDATION_LABEL_TARGET = int(os.getenv("VALIDATION_LABEL_TARGET", "150"))

PUBLIC_SIGNAL_KEYS = {
    "mode",
    "source",
    "tilt_risk",
    "readiness",
    "recovery",
    "jaw_tension",
    "shoulder_tension",
    "signal_confidence",
    "face_detected",
    "shoulders_visible",
    "fps",
    "latency_ms",
    "jaw_score",
    "brow_tension",
    "eye_tension",
    "eye_widen",
    "mouth_pressure",
    "lip_compression",
    "sneer",
    "facial_tension",
    "facial_arousal",
    "dominant_facial",
    "dominant_facial_value",
    "head_drift",
    "shoulder_score",
    "forward_head",
    "shoulder_protraction",
    "head_forward_z",
    "torso_lean",
    "posture_stress",
    "dominant_posture",
    "dominant_posture_value",
    "motion",
    "brightness",
    "raw_landmarks",
    "derived_points",
    "signal_layers",
    "runtime_source",
    "coach_provider",
    "coach_model",
    "recommendation",
    "tester_id",
    "game",
    "page_url",
    "jaw_confidence",
    "shoulder_confidence",
    "face_confidence",
    "pose_confidence",
    "is_fallback",
    "quality_score",
    "quality_gate_ok",
    "quality_issues",
}

COACH_RATE_LIMIT_SECONDS = 10.0
_coach_cache: dict[str, dict[str, Any]] = {}


def _safe_relative(path: Path) -> str:
    """Return ``path`` relative to APP_ROOT when possible, str(path) otherwise.

    Tests run with a temp DATA_DIR outside the repo; the previous code
    crashed there because ``Path.relative_to`` insists on a subpath.
    Production callers still get the short relative form."""
    try:
        return str(path.relative_to(APP_ROOT))
    except ValueError:
        return str(path)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return scoring.clamp(value, low, high)


def as_float(value: Any, fallback: float = 0.0) -> float:
    return scoring.as_float(value, fallback)


def read_dotenv() -> dict[str, str]:
    env_path = APP_ROOT / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_value(name: str, fallback: str = "") -> str:
    return os.getenv(name, "").strip() or read_dotenv().get(name, fallback).strip()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_overlay_state() -> dict[str, Any]:
    return read_json(OVERLAY_STATE_PATH) or {}


def tail_lines(path: Path, limit: int = 300) -> list[str]:
    if not path.exists():
        return []
    # ``utf-8-sig`` swallows a leading BOM if the file was created by
    # legacy tools. Without this, a BOM byte sequence sneaks into the
    # first record and breaks json.loads in tail_jsonl.
    lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    return [line for line in lines[-limit:] if line.strip()]


def tail_jsonl(path: Path, limit: int = 300) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in tail_lines(path, limit):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def public_tester_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "anonymous"
    if "@" in text:
        name, _, domain = text.partition("@")
        return f"{name[:2]}***@{domain}"
    if len(text) > 18:
        return f"{text[:15]}..."
    return text


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def unique_sessions(rows: list[dict[str, Any]]) -> int:
    sessions = {
        str(row.get("session_id"))
        for row in rows
        if row.get("session_id") not in {None, ""}
    }
    return len(sessions)


def feature_readiness(features: dict[str, Any]) -> float:
    return scoring.feature_readiness(features)


def recent_samples(limit: int = 600) -> list[dict[str, Any]]:
    return tail_jsonl(AFFECTIVE_DATASET_PATH, limit)


def latest_embedding() -> dict[str, Any] | None:
    rows = tail_jsonl(EMBEDDINGS_PATH, 1)
    return rows[-1] if rows else None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def similar_body_states(limit: int = 6) -> list[dict[str, Any]]:
    current = latest_embedding()
    if not current:
        return []
    current_vector = current.get("embedding") or []
    rows = tail_jsonl(EMBEDDINGS_PATH, 900)
    matches: list[dict[str, Any]] = []
    for row in rows:
        if row is current:
            continue
        score = cosine(current_vector, row.get("embedding") or [])
        matches.append(
            {
                "timestamp": row.get("timestamp"),
                "session_id": row.get("session_id"),
                "similarity": round(score, 3),
                "tilt": round(as_float(row.get("tilt")), 1),
                "tokens": row.get("tokens") or [],
                "phrase": row.get("current_phrase", ""),
            }
        )
    return sorted(matches, key=lambda item: item["similarity"], reverse=True)[:limit]


def investor_metrics() -> dict[str, Any]:
    samples = tail_jsonl(AFFECTIVE_DATASET_PATH, 100_000)
    embeddings = tail_jsonl(EMBEDDINGS_PATH, 100_000)
    feedback = tail_jsonl(FEEDBACK_PATH, 100_000)
    overlay = read_overlay_state()
    report = session_report()
    proof = proof_card()
    command = command_effectiveness()
    privacy = privacy_passport()

    labels = {"felt_tension": 0, "helped": 0, "false_alert": 0}
    for row in feedback:
        payload = row.get("payload") or {}
        for key in labels:
            if payload.get(key):
                labels[key] += 1

    recent_feedback = []
    for row in feedback[-8:][::-1]:
        payload = row.get("payload") or {}
        recent_feedback.append(
            {
                "timestamp": row.get("timestamp"),
                "felt_tension": bool(payload.get("felt_tension")),
                "helped": bool(payload.get("helped")),
                "false_alert": bool(payload.get("false_alert")),
                "moment": payload.get("moment") or payload.get("source") or "",
            }
        )

    tilt_values = [as_float((row.get("features") or {}).get("tilt_score")) for row in samples]
    avg_tilt = round(sum(tilt_values) / max(len(tilt_values), 1), 1) if tilt_values else 0.0
    max_tilt = round(max(tilt_values), 1) if tilt_values else 0.0
    total_alerts = sum(labels.values())
    return {
        "status": "ready",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "sessions_recorded": unique_sessions(samples),
        "body_state_samples": len(samples),
        "embedding_vectors": len(embeddings),
        "feedback_labels": len(feedback),
        "labels": labels,
        "help_rate_percent": round(labels["helped"] / total_alerts * 100, 1) if total_alerts else None,
        "false_alert_rate_percent": round(labels["false_alert"] / total_alerts * 100, 1) if total_alerts else None,
        "recent_feedback": recent_feedback,
        "avg_tilt": avg_tilt,
        "max_tilt": max_tilt,
        "recovery_delta": report.get("recovery_delta", 0),
        "recovery_seconds": report.get("recovery_seconds"),
        "tilt_delta_points": command.get("tilt_delta"),
        "best_command": command.get("best_command"),
        "proof_headline": proof.get("headline"),
        "proof_source": proof.get("source"),
        "proof_is_synthetic": bool(proof.get("is_synthetic")),
        "live_running": bool(overlay.get("running")),
        "privacy_raw_video_saved": bool(privacy.get("raw_video_saved")),
        "data_files": privacy.get("data_files", []),
        "next_validation_target": {
            "tester_sessions": VALIDATION_TESTER_TARGET,
            "feedback_labels": VALIDATION_LABEL_TARGET,
            "target_false_alert_rate_percent": "< 25",
            "target_help_rate_percent": "> 40",
            "target_recovery_delta": "> 20 tilt points",
        },
    }


def cohort_summary(days: int = 7, validation_goal: int = VALIDATION_LABEL_TARGET) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, int(days)))
    all_feedback = tail_jsonl(FEEDBACK_PATH, 100_000)
    recent_feedback = [
        row for row in all_feedback if (parse_timestamp(row.get("timestamp")) or now) >= cutoff
    ]
    labels = {"felt_tension": 0, "helped": 0, "false_alert": 0}
    labelled_records = []
    testers: set[str] = set()
    for row in recent_feedback:
        payload = row.get("payload") or {}
        tester = public_tester_label(
            payload.get("tester_id")
            or payload.get("name")
            or payload.get("tester")
            or payload.get("telegram")
            or payload.get("email")
            or payload.get("source")
        )
        if tester and tester != "anonymous":
            testers.add(tester)
        has_label = False
        for key in labels:
            if payload.get(key):
                labels[key] += 1
                has_label = True
        if has_label:
            labelled_records.append(row)

    total_labelled = len(labelled_records)
    command = command_effectiveness()
    report = session_report()
    deltas = [as_float(value, 0.0) for value in (command.get("tilt_delta"), report.get("recovery_delta")) if as_float(value, 0.0) > 0]
    recovery_seconds = [as_float(value, -1.0) for value in (command.get("recovery_seconds"), report.get("recovery_seconds")) if as_float(value, -1.0) >= 0]
    recent = []
    for row in labelled_records[-10:][::-1]:
        payload = row.get("payload") or {}
        label = "felt_tension"
        if payload.get("helped"):
            label = "helped"
        elif payload.get("false_alert"):
            label = "false_alert"
        recent.append(
            {
                "timestamp": row.get("timestamp"),
                "label": label,
                "moment": payload.get("moment") or payload.get("source") or "session",
                "tester": public_tester_label(payload.get("name") or payload.get("tester") or payload.get("telegram") or payload.get("email") or payload.get("source")),
                "mode": (payload.get("state") or {}).get("mode") or payload.get("last_alert_level") or "",
            }
        )
    return {
        "status": "ready",
        "window_days": max(1, int(days)),
        "generated_at": now.isoformat(timespec="milliseconds"),
        "feedback_records": len(recent_feedback),
        "total_alerts_labelled": total_labelled,
        "unique_testers": len(testers),
        "tester_target": VALIDATION_TESTER_TARGET,
        "testers_remaining": max(0, VALIDATION_TESTER_TARGET - len(testers)),
        "validation_goal": int(validation_goal),
        "labels_target": int(validation_goal),
        "labels_remaining": max(0, int(validation_goal) - total_labelled),
        "progress_percent": round(total_labelled / max(int(validation_goal), 1) * 100, 1),
        "labels": labels,
        "help_rate_percent": round(labels["helped"] / total_labelled * 100, 1) if total_labelled else None,
        "false_alert_rate_percent": round(labels["false_alert"] / total_labelled * 100, 1) if total_labelled else None,
        "median_tilt_delta": round(median(deltas), 1) if deltas else None,
        "median_recovery_seconds": round(median(recovery_seconds), 1) if recovery_seconds else None,
        "best_command": command.get("best_command"),
        "latest_testers": recent,
        "next_milestone": {
            "goal": int(validation_goal),
            "tester_goal": VALIDATION_TESTER_TARGET,
            "why_it_matters": "Human feedback labels turn the heuristic MVP into a supervised validation dataset.",
            "needed": max(0, int(validation_goal) - total_labelled),
            "testers_needed": max(0, VALIDATION_TESTER_TARGET - len(testers)),
        },
    }


def session_replay(limit: int = 800) -> dict[str, Any]:
    samples = recent_samples(limit)
    if not samples:
        return {"status": "empty", "points": [], "markers": [], "message": "Run a live session to build a replay."}
    points = []
    for index, sample in enumerate(samples):
        features = sample.get("features") or {}
        points.append(
            {
                "i": index,
                "timestamp": sample.get("timestamp"),
                "tilt": round(as_float(features.get("tilt_score"), as_float(sample.get("tilt"), 0.0)), 1),
                "readiness": round(feature_readiness(features), 1),
                "jaw": round(as_float(features.get("jaw_clench_score"), 0.0), 1),
                "shoulders": round(as_float(features.get("shoulder_elevation_score"), 0.0), 1),
                "triggers": sample.get("triggers") or [],
                "event": sample.get("event") or sample.get("game_event") or "",
            }
        )
    peak_index = max(range(len(points)), key=lambda idx: points[idx]["tilt"])
    recovery_point = min(points[peak_index:], key=lambda point: point["tilt"])
    markers = [
        {"type": "peak", "i": peak_index, "label": "Peak tilt"},
        {"type": "recovery", "i": recovery_point["i"], "label": "Recovery low"},
    ]
    for row in tail_jsonl(FEEDBACK_PATH, 40)[-8:]:
        payload = row.get("payload") or {}
        if not any(payload.get(key) for key in ("felt_tension", "helped", "false_alert")):
            continue
        label = "felt tilt"
        if payload.get("helped"):
            label = "helped"
        elif payload.get("false_alert"):
            label = "false alert"
        markers.append({"type": "label", "i": len(points) - 1, "timestamp": row.get("timestamp"), "label": label})
    return {
        "status": "ready",
        "points": points,
        "markers": markers,
        "peak_tilt": points[peak_index]["tilt"],
        "recovery_tilt": recovery_point["tilt"],
        "samples": len(points),
    }


def safe_profile_id(value: Any) -> str:
    text = str(value or "default").strip().lower()
    text = re.sub(r"[^a-z0-9а-яё_-]+", "_", text, flags=re.IGNORECASE)
    return (text.strip("._-") or "default")[:48]


def profile_path(profile_id: Any) -> Path:
    return PROFILES_DIR / f"{safe_profile_id(profile_id)}.json"


def save_baseline_profile(payload: dict[str, Any]) -> dict[str, Any]:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(payload or {})
    profile_id = safe_profile_id(payload.get("profile_id") or payload.get("id") or "default")
    forbidden = {"raw_video", "video", "frame", "frames", "audio", "image", "screenshot"}
    clean_payload = {key: value for key, value in payload.items() if str(key).lower() not in forbidden}
    record = {
        "schema": "personal_baseline_v2",
        "profile_id": profile_id,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "local_first": True,
        "raw_video_saved": False,
        "raw_audio_saved": False,
        "baseline": clean_payload.get("baseline") or clean_payload.get("phases") or clean_payload,
    }
    atomic_io.atomic_write_json(profile_path(profile_id), record)
    return {"ok": True, "profile_id": profile_id, "stored_at": _safe_relative(profile_path(profile_id)), "profile": record}


def load_baseline_profile(profile_id: Any = "default") -> dict[str, Any]:
    record = read_json(profile_path(profile_id))
    if not record:
        return {"ok": False, "profile_id": safe_profile_id(profile_id), "message": "Профиль baseline пока не сохранён."}
    return {"ok": True, "profile_id": safe_profile_id(profile_id), "profile": record}


def update_profile_from_calm_signal(profile_id: Any, signals: dict[str, Any]) -> dict[str, Any]:
    """Online baseline adaptation from calm windows only.

    This never stores frames. It slowly updates a few derived neutral values
    when tilt is low, confidence is high, and the player is not in recovery.
    """
    profile_id = safe_profile_id(profile_id or "anonymous")
    tilt = as_float(signals.get("tilt_risk"), 100.0)
    confidence = as_float(signals.get("signal_confidence"), 0.0)
    if tilt > 35 or confidence < 0.6 or signals.get("jaw_tension") == "high" or signals.get("shoulder_tension") == "high":
        return {"ok": True, "updated": False, "reason": "not_calm_window"}
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    current = read_json(profile_path(profile_id)) or {
        "schema": "personal_baseline_v2",
        "profile_id": profile_id,
        "local_first": True,
        "raw_video_saved": False,
        "raw_audio_saved": False,
        "baseline": {},
    }
    adaptive = dict((current.get("baseline") or {}).get("adaptive_neutral") or {})
    alpha = 0.035
    for key in (
        "jaw_score",
        "shoulder_score",
        "facial_tension",
        "posture_stress",
        "forward_head",
        "shoulder_protraction",
        "motion",
        "brightness",
    ):
        value = signals.get(key)
        if value is None:
            continue
        previous = as_float(adaptive.get(key), as_float(value))
        adaptive[key] = round(previous * (1 - alpha) + as_float(value) * alpha, 4)
    baseline = dict(current.get("baseline") or {})
    baseline["adaptive_neutral"] = adaptive
    current.update(
        {
            "updated_at": _utc_now_iso(),
            "profile_id": profile_id,
            "baseline": baseline,
            "adaptation": {
                "last_calm_update_at": _utc_now_iso(),
                "alpha": alpha,
                "source": "derived_browser_signals",
            },
        }
    )
    atomic_io.atomic_write_json(profile_path(profile_id), current)
    return {"ok": True, "updated": True, "profile_id": profile_id}


def maybe_harvest_passive_label(session_id: str, tester_id: str, game: str, signals: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    tilt = as_float(signals.get("tilt_risk"), 0.0)
    recovery = as_float(signals.get("recovery"), 0.0)
    recommendation = str(signals.get("recommendation") or "").strip()
    event = None
    if tilt >= 78:
        event = "implicit_sustained_high_tilt"
    elif recovery >= 62 and recommendation:
        event = "implicit_recovery_after_command"
    if not event:
        return {"ok": True, "harvested": False}
    last_at = parse_timestamp(state.get("last_passive_label_at"))
    if last_at and (_utc_now() - last_at.astimezone(timezone.utc)).total_seconds() < 15:
        return {"ok": True, "harvested": False, "reason": "cooldown"}
    record = {
        "timestamp": _utc_now_iso(),
        "schema": "validation_study_v1",
        "event": event,
        "tester_id": tester_id,
        "game": game,
        "payload": {
            "session_id": session_id,
            "implicit": True,
            "source": "browser_cv",
            "tilt_risk": tilt,
            "recovery": recovery,
            "recommendation": recommendation,
            "privacy": {"raw_video_saved": False, "raw_audio_saved": False, "derived_signals_only": True},
        },
    }
    atomic_io.atomic_append_jsonl(VALIDATION_STUDY_PATH, record)
    return {"ok": True, "harvested": True, "event": event, "record": record}


def feedback_rows_for_session(session_id: str | None) -> list[dict[str, Any]]:
    rows = tail_jsonl(FEEDBACK_PATH, 100_000)
    if not session_id:
        return rows
    return [row for row in rows if (row.get("payload") or {}).get("session_id") == session_id]


def public_session_score(session_id: str | None = None) -> dict[str, Any]:
    timeline = public_session_timeline(session_id, limit=1000)
    summary = timeline.get("summary") or public_session_summary(session_id)
    points = timeline.get("points") or []
    labels = feedback_rows_for_session(summary.get("session_id"))
    helped = any((row.get("payload") or {}).get("helped") for row in labels)
    false_alert = any((row.get("payload") or {}).get("false_alert") for row in labels)
    felt_tension = any((row.get("payload") or {}).get("felt_tension") for row in labels)
    recovery_seconds = None
    if points:
        peak = max(points, key=lambda point: as_float(point.get("tilt_risk")))
        target = max(45.0, as_float(peak.get("tilt_risk")) - 20.0)
        for point in points:
            if as_float(point.get("t")) >= as_float(peak.get("t")) and as_float(point.get("tilt_risk")) <= target:
                recovery_seconds = round(as_float(point.get("t")) - as_float(peak.get("t")), 1)
                break
    return {
        "status": "ready" if points else "empty",
        "session_id": summary.get("session_id"),
        "tester_id": summary.get("tester_id"),
        "game": summary.get("game"),
        "samples": summary.get("samples", 0),
        "max_tilt": summary.get("max_tilt"),
        "avg_readiness": summary.get("avg_readiness"),
        "avg_recovery": summary.get("avg_recovery"),
        "recovery_delta": summary.get("recovery_delta"),
        "recovery_seconds": recovery_seconds,
        "dominant_lock": summary.get("dominant_lock"),
        "command_that_worked": summary.get("command_that_worked"),
        "tester_confirmed_help": helped,
        "tester_felt_tension": felt_tension,
        "tester_marked_false_alert": false_alert,
        "labels": len(labels),
        "message": "Подсказка подтверждена тестером." if helped else "Нужно больше labels: попросите тестера нажать H, если команда помогла.",
    }


def update_coach_memory_from_feedback(record: dict[str, Any]) -> None:
    payload = record.get("payload") or {}
    last_signals = payload.get("last_signals") or {}
    command = str(last_signals.get("recommendation") or payload.get("command") or "").strip()
    if not command:
        return
    memory = {
        "timestamp": record.get("timestamp"),
        "schema": "coach_memory_v1",
        "session_id": payload.get("session_id"),
        "tester_id": public_tester_label(payload.get("tester_id") or "anonymous"),
        "game": str(payload.get("game") or "")[:80],
        "command": command[:240],
        "helped": bool(payload.get("helped")),
        "false_alert": bool(payload.get("false_alert")),
        "felt_tension": bool(payload.get("felt_tension")),
        "tilt_risk": as_float(last_signals.get("tilt_risk")),
        "readiness": as_float(last_signals.get("readiness")),
        "recovery": as_float(last_signals.get("recovery")),
        "dominant_lock": "jaw" if last_signals.get("jaw_tension") == "high" else "shoulders" if last_signals.get("shoulder_tension") == "high" else "mixed",
    }
    atomic_io.atomic_append_jsonl(COACH_MEMORY_PATH, memory)


def update_coach_memory_from_recovery(
    session_id: str,
    tester_id: str,
    game: str,
    signals: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Store passive recovery evidence from derived signals only.

    This is not a human label. It is a candidate proof that a command was
    followed by a measurable tilt drop, useful for ranking commands before
    enough H/F/T labels exist.
    """
    tilt = as_float(signals.get("tilt_risk"), -1.0)
    if tilt < 0:
        return {"ok": True, "stored": False, "reason": "no_tilt"}
    peak = as_float(state.get("recovery_peak_tilt"), 0.0)
    peak_at = parse_timestamp(state.get("recovery_peak_at"))
    recommendation = str(signals.get("recommendation") or state.get("last_recommendation") or "").strip()
    if tilt >= 65 and tilt >= peak:
        state["recovery_peak_tilt"] = tilt
        state["recovery_peak_at"] = _utc_now_iso()
        if recommendation:
            state["last_recommendation"] = recommendation
        return {"ok": True, "stored": False, "reason": "tracking_peak"}
    if not peak_at or peak < 65 or tilt > peak - 18:
        if recommendation:
            state["last_recommendation"] = recommendation
        return {"ok": True, "stored": False, "reason": "waiting_recovery"}
    last_at = parse_timestamp(state.get("last_recovery_memory_at"))
    if last_at and (_utc_now() - last_at.astimezone(timezone.utc)).total_seconds() < 20:
        return {"ok": True, "stored": False, "reason": "cooldown"}
    recovery_seconds = round((_utc_now() - peak_at.astimezone(timezone.utc)).total_seconds(), 1)
    memory = {
        "timestamp": _utc_now_iso(),
        "schema": "coach_memory_v1",
        "session_id": session_id,
        "tester_id": public_tester_label(tester_id or "anonymous"),
        "game": str(game or "")[:80],
        "command": recommendation[:240] if recommendation else "local coach",
        "helped": False,
        "false_alert": False,
        "felt_tension": False,
        "passive_recovery": True,
        "evidence_type": "derived_recovery_after_command",
        "tilt_before": round(peak, 1),
        "tilt_after": round(tilt, 1),
        "recovery_delta": round(peak - tilt, 1),
        "recovery_seconds": recovery_seconds,
        "tilt_risk": tilt,
        "readiness": as_float(signals.get("readiness")),
        "recovery": as_float(signals.get("recovery")),
        "dominant_lock": "jaw" if signals.get("jaw_tension") == "high" else "shoulders" if signals.get("shoulder_tension") == "high" else "mixed",
    }
    atomic_io.atomic_append_jsonl(COACH_MEMORY_PATH, memory)
    state["last_recovery_memory_at"] = memory["timestamp"]
    state["recovery_peak_tilt"] = tilt
    state["recovery_peak_at"] = memory["timestamp"]
    return {"ok": True, "stored": True, "record": memory}


def coach_memory(tester_id: Any = None, game: Any = None) -> dict[str, Any]:
    tester = public_tester_label(tester_id or "")
    game_text = str(game or "").strip().lower()
    rows = tail_jsonl(COACH_MEMORY_PATH, 100_000)
    if tester:
        rows = [row for row in rows if public_tester_label(row.get("tester_id")) == tester]
    if game_text:
        rows = [row for row in rows if str(row.get("game") or "").lower() == game_text]
    by_command: dict[str, dict[str, Any]] = {}
    for row in rows:
        command = str(row.get("command") or "").strip()
        if not command:
            continue
        item = by_command.setdefault(command, {"command": command, "uses": 0, "helped": 0, "false_alert": 0, "felt_tension": 0, "passive_recovery": 0, "recovery_delta_sum": 0.0, "tilt_sum": 0.0, "locks": {}})
        item["uses"] += 1
        item["helped"] += 1 if row.get("helped") else 0
        item["false_alert"] += 1 if row.get("false_alert") else 0
        item["felt_tension"] += 1 if row.get("felt_tension") else 0
        item["passive_recovery"] += 1 if row.get("passive_recovery") else 0
        item["recovery_delta_sum"] += as_float(row.get("recovery_delta"))
        item["tilt_sum"] += as_float(row.get("tilt_risk"))
        lock = str(row.get("dominant_lock") or "mixed")
        item["locks"][lock] = item["locks"].get(lock, 0) + 1
    commands = []
    for item in by_command.values():
        uses = max(1, int(item["uses"]))
        locks = item.pop("locks")
        item["help_rate_percent"] = round(item["helped"] / uses * 100, 1)
        item["false_alert_rate_percent"] = round(item["false_alert"] / uses * 100, 1)
        item["recovery_proofs"] = item["passive_recovery"]
        item["avg_recovery_delta"] = round(item.pop("recovery_delta_sum") / uses, 1)
        item["avg_tilt_when_used"] = round(item.pop("tilt_sum") / uses, 1)
        item["dominant_lock"] = max(locks.items(), key=lambda pair: pair[1])[0] if locks else "mixed"
        commands.append(item)
    commands.sort(key=lambda item: (item["help_rate_percent"], item["recovery_proofs"], item["avg_recovery_delta"], item["uses"], -item["false_alert_rate_percent"]), reverse=True)
    return {
        "status": "ready",
        "tester_id": tester or "all",
        "game": game_text or "all",
        "memory_events": len(rows),
        "commands": commands[:12],
        "best_command": commands[0] if commands else None,
        "message": "Coach Memory усиливается с каждым label: H/F/T во время сессии.",
    }


def command_effectiveness_table() -> dict[str, Any]:
    feedback = tail_jsonl(FEEDBACK_PATH, 100_000)
    command = command_effectiveness()
    labels = {"helped": 0, "felt_tension": 0, "false_alert": 0}
    for row in feedback:
        payload = row.get("payload") or {}
        for key in labels:
            if payload.get(key):
                labels[key] += 1
    total = sum(labels.values())
    rows = [
        {
            "command": command.get("best_command"),
            "uses": max(total, 1) if command.get("source") != "demo" else 0,
            "avg_tilt_delta": command.get("tilt_delta"),
            "avg_recovery_seconds": command.get("recovery_seconds"),
            "help_rate_percent": round(labels["helped"] / total * 100, 1) if total else None,
            "false_alert_rate_percent": round(labels["false_alert"] / total * 100, 1) if total else None,
            "source": command.get("source"),
            "claim": "current_session" if command.get("source") in {"real", "weak_signal"} else "fallback_template",
        },
        {"command": "Челюсть мягко. Плечи вниз. Выдох.", "uses": 0, "avg_tilt_delta": None, "avg_recovery_seconds": None, "help_rate_percent": None, "false_alert_rate_percent": None, "source": "local_fallback", "claim": "needs_labels"},
        {"command": "Сохраняй мягкость. Не зажимай дыхание.", "uses": 0, "avg_tilt_delta": None, "avg_recovery_seconds": None, "help_rate_percent": None, "false_alert_rate_percent": None, "source": "local_fallback", "claim": "needs_labels"},
    ]
    return {"status": "ready", "labels": labels, "total_labels": total, "rows": rows, "message": "Rows marked needs_labels are templates, not validated winners yet."}


def coach_command_library() -> dict[str, Any]:
    """Curated local command library for fast fallback coaching."""
    commands = [
        {"id": "jaw_soft", "driver": "jaw", "command": "Челюсть мягко. Выдох длиннее.", "duration_seconds": 20},
        {"id": "shoulders_down", "driver": "shoulders", "command": "Плечи вниз. Шея свободна.", "duration_seconds": 20},
        {"id": "eyes_wide", "driver": "eyes", "command": "Расширь взгляд. Не вцепляйся в экран.", "duration_seconds": 15},
        {"id": "breath_reset", "driver": "breath", "command": "Выдохни. Верни вес в таз.", "duration_seconds": 20},
        {"id": "clutch_anchor", "driver": "clutch", "command": "Мягкая ось. Один следующий выбор.", "duration_seconds": 10},
    ]
    effectiveness = command_effectiveness_table()
    current = effectiveness.get("rows", [{}])[0]
    return {
        "status": "ready",
        "commands": commands,
        "validated_current": current,
        "message": "Library is local fallback first; effectiveness becomes personalized after enough labels.",
    }


def false_alert_review() -> dict[str, Any]:
    feedback = tail_jsonl(FEEDBACK_PATH, 100_000)
    false_rows = [row for row in feedback if (row.get("payload") or {}).get("false_alert")]
    recent = []
    for row in false_rows[-12:][::-1]:
        payload = row.get("payload") or {}
        state = payload.get("state") or {}
        recent.append(
            {
                "timestamp": row.get("timestamp"),
                "moment": payload.get("moment") or payload.get("source") or "session",
                "mode": state.get("mode") or payload.get("last_alert_level") or "",
                "confidence": payload.get("confidence") or state.get("signal_confidence"),
                "tilt": state.get("tilt_risk"),
                "likely_reason": (
                    "low_confidence" if as_float(payload.get("confidence") or state.get("signal_confidence"), 1.0) < 0.55
                    else "threshold_or_context"
                ),
            }
        )
    total_labels = sum(
        1
        for row in feedback
        if any((row.get("payload") or {}).get(key) for key in ("felt_tension", "helped", "false_alert"))
    )
    return {
        "status": "ready",
        "false_alerts": len(false_rows),
        "total_labels": total_labels,
        "false_alert_rate_percent": round(len(false_rows) / total_labels * 100, 1) if total_labels else None,
        "recent": recent,
        "suggested_action": "Collect 150+ labels, then tune thresholds per player baseline instead of global defaults.",
    }


def personal_model_score(profile_id: Any = "default") -> dict[str, Any]:
    profile = load_baseline_profile(profile_id)
    overlay = read_overlay_state()
    metrics = overlay.get("metrics") or {}
    current = {
        "tilt": as_float(overlay.get("tilt_score"), 0.0),
        "jaw_score": as_float(metrics.get("jaw_clench_score"), 0.0),
        "shoulder_score": as_float(metrics.get("shoulder_elevation_score"), 0.0),
        "confidence": as_float(overlay.get("face_tracking_quality"), 0.0) / 100.0,
    }
    if not profile.get("ok"):
        return {
            "status": "missing_baseline",
            "profile_id": safe_profile_id(profile_id),
            "current": current,
            "score": None,
            "message": "Сохраните personal baseline: neutral, jaw, shoulders, release.",
        }
    baseline = (profile.get("profile") or {}).get("baseline") or {}
    neutral = baseline.get("neutral") or {}
    jaw_delta = current["jaw_score"] - as_float(neutral.get("jaw_score"), 0.0)
    shoulder_delta = current["shoulder_score"] - as_float(neutral.get("shoulder_score"), 0.0)
    tilt_delta = current["tilt"] - as_float(neutral.get("tilt"), 0.0)
    score = clamp(50 + jaw_delta * 0.35 + shoulder_delta * 0.35 + tilt_delta * 0.3)
    return {
        "status": "ready",
        "profile_id": safe_profile_id(profile_id),
        "score": round(score, 1),
        "jaw_delta": round(jaw_delta, 1),
        "shoulder_delta": round(shoulder_delta, 1),
        "tilt_delta": round(tilt_delta, 1),
        "current": current,
        "message": (
            f"Сегодня напряжение выше baseline на {round(score - 50, 1)} пунктов."
            if score >= 55 else "Состояние близко к личному baseline."
        ),
    }


def autopilot_v2() -> dict[str, Any]:
    overlay = read_overlay_state()
    metrics = overlay.get("metrics") or {}
    tilt = as_float(overlay.get("tilt_score"), 0.0)
    jaw = as_float(metrics.get("jaw_clench_score"), 0.0)
    shoulders = as_float(metrics.get("shoulder_elevation_score"), 0.0)
    recovery = as_float((overlay.get("recovery") or {}).get("score"), 0.0)
    if not overlay.get("running"):
        return {"status": "idle", "prediction": "waiting_for_live_session", "lead_time_seconds": None, "intervention": "none"}
    driver = "jaw" if jaw >= shoulders else "shoulders"
    risk = clamp(tilt * 0.45 + jaw * 0.28 + shoulders * 0.27)
    if risk >= 70:
        lead = 0
        intervention = "reset_now"
    elif risk >= 52:
        lead = 10
        intervention = "jaw_soft" if driver == "jaw" else "shoulders_down"
    elif recovery >= 60:
        lead = 15
        intervention = "maintain_recovery"
    else:
        lead = None
        intervention = "watch"
    return {
        "status": "ready",
        "risk": round(risk, 1),
        "driver": driver,
        "lead_time_seconds": lead,
        "intervention": intervention,
        "prediction": (
            f"Через ~{lead} сек вероятен {driver} lock." if lead and intervention not in {"maintain_recovery", "watch"}
            else "Recovery likely improving." if intervention == "maintain_recovery"
            else "Тильт уже близко/в красной зоне." if intervention == "reset_now"
            else "Паттерн стабилен."
        ),
        "command": {
            "jaw_soft": "Челюсть мягко. Выдох длиннее.",
            "shoulders_down": "Плечи вниз. Шея свободна.",
            "reset_now": "Челюсть мягко. Плечи вниз. Выдох.",
            "maintain_recovery": "Сохраняй мягкость. Не зажимай дыхание.",
            "watch": "Команда не нужна.",
        }.get(intervention, "Команда не нужна."),
    }


def append_study_event(payload: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(payload or {})
    event = str(payload.get("event") or "note")[:64]
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "schema": "validation_study_v1",
        "event": event,
        "tester_id": public_tester_label(payload.get("tester_id") or payload.get("name") or "anonymous"),
        "game": str(payload.get("game") or "")[:80],
        "payload": payload,
    }
    atomic_io.atomic_append_jsonl(VALIDATION_STUDY_PATH, record)
    return {"ok": True, "stored_at": _safe_relative(VALIDATION_STUDY_PATH), "record": record}


def public_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"web-{stamp}-{uuid.uuid4().hex[:8]}"


def scalar_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    if isinstance(value, str):
        return value[:240]
    return None


def sanitize_public_signal(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only derived browser-CV fields; never persist frames/video/audio."""
    clean: dict[str, Any] = {}
    for key in PUBLIC_SIGNAL_KEYS:
        if key in payload:
            value = scalar_value(payload.get(key))
            if value is not None:
                clean[key] = value
    return clean


def latest_public_session() -> dict[str, Any]:
    return read_json(PUBLIC_SESSION_STATE_PATH) or {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat(timespec="milliseconds")


def _record_date(timestamp: Any = None) -> str:
    parsed = parse_timestamp(timestamp) if timestamp else None
    return (parsed or _utc_now()).astimezone(timezone.utc).date().isoformat()


def append_write_queue(kind: str, payload: dict[str, Any], reason: str = "client_offline") -> dict[str, Any]:
    """Durable write-ahead queue for events that could not be committed live.

    The browser also keeps a localStorage queue, but this file is the local
    machine's dead-letter log. It lets us replay failed writes without losing
    labels or session boundaries. Only derived signals and labels are accepted.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "queued_at": _utc_now_iso(),
        "schema": "write_queue_v1",
        "kind": str(kind or "unknown")[:64],
        "reason": str(reason or "unknown")[:160],
        "payload": dict(payload or {}),
        "privacy": {
            "raw_video_saved": False,
            "raw_audio_saved": False,
            "derived_signals_only": True,
        },
    }
    atomic_io.atomic_append_jsonl(WRITE_QUEUE_PATH, record)
    return {"ok": True, "queued": True, "stored_at": _safe_relative(WRITE_QUEUE_PATH), "record": record}


def _commit_queued_record(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("kind") or "").strip()
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    if kind in {"signal", "public_signal", "signals", "/api/signals"}:
        return append_public_signal(payload)
    if kind in {"feedback", "label", "/api/feedback"}:
        return append_feedback(payload)
    if kind in {"session", "public_session", "/api/session"}:
        return handle_public_session(payload)
    if kind in {"heartbeat", "session_heartbeat", "/api/session-heartbeat"}:
        return public_session_heartbeat(payload)
    if kind in {"study_event", "/api/study-event"}:
        return append_study_event(payload)
    if kind in {"baseline_profile", "/api/baseline-profile"}:
        return save_baseline_profile(payload)
    return {"ok": False, "error": f"unknown_queue_kind:{kind}"}


def flush_write_queue(limit: int = 200) -> dict[str, Any]:
    rows = tail_jsonl(WRITE_QUEUE_PATH, max(1, limit))
    if not rows:
        return {"ok": True, "processed": 0, "replayed": 0, "remaining": 0, "errors": []}
    processed = 0
    replayed = 0
    errors: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for row in rows:
        processed += 1
        try:
            result = _commit_queued_record(row)
            if result.get("ok"):
                replayed += 1
            else:
                errors.append({"kind": row.get("kind"), "error": result.get("error") or "not_ok"})
                remaining.append(row)
        except Exception as exc:  # pragma: no cover - defensive disk path
            errors.append({"kind": row.get("kind"), "error": str(exc)[:240]})
            remaining.append(row)

    # Keep any lines that were not part of this replay plus failed records.
    all_rows = tail_jsonl(WRITE_QUEUE_PATH, 100_000)
    untouched = all_rows[:-len(rows)] if len(all_rows) > len(rows) else []
    if untouched or remaining:
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in [*remaining, *untouched])
        atomic_io.atomic_write_text(WRITE_QUEUE_PATH, content)
    else:
        atomic_io.atomic_write_text(WRITE_QUEUE_PATH, "")
    return {"ok": True, "processed": processed, "replayed": replayed, "remaining": len(remaining) + len(untouched), "errors": errors[:20]}


def _daily_rollup_for(day: str | None = None) -> dict[str, Any]:
    day = day or _record_date()
    signals_rows = [row for row in tail_jsonl(PUBLIC_SIGNALS_PATH, 100_000) if _record_date(row.get("timestamp")) == day]
    session_rows = [row for row in tail_jsonl(PUBLIC_SESSIONS_PATH, 100_000) if _record_date(row.get("timestamp")) == day]
    feedback_rows = [row for row in tail_jsonl(FEEDBACK_PATH, 100_000) if _record_date(row.get("timestamp")) == day]
    signals = [row.get("signals") or {} for row in signals_rows]
    tilts = [as_float(row.get("tilt_risk")) for row in signals if row.get("tilt_risk") is not None]
    readiness = [as_float(row.get("readiness")) for row in signals if row.get("readiness") is not None]
    recovery = [as_float(row.get("recovery")) for row in signals if row.get("recovery") is not None]
    helped = sum(1 for row in feedback_rows if (row.get("payload") or {}).get("helped"))
    false_alerts = sum(1 for row in feedback_rows if (row.get("payload") or {}).get("false_alert"))
    felt_tension = sum(1 for row in feedback_rows if (row.get("payload") or {}).get("felt_tension"))
    labels = helped + false_alerts + felt_tension
    return {
        "date": day,
        "updated_at": _utc_now_iso(),
        "sessions": unique_sessions(session_rows),
        "session_events": len(session_rows),
        "signal_samples": len(signals_rows),
        "feedback_records": len(feedback_rows),
        "labels": {"total": labels, "helped": helped, "false_alert": false_alerts, "felt_tension": felt_tension},
        "avg_tilt": round(sum(tilts) / max(len(tilts), 1), 2) if tilts else None,
        "peak_tilt": round(max(tilts), 2) if tilts else None,
        "avg_readiness": round(sum(readiness) / max(len(readiness), 1), 2) if readiness else None,
        "avg_recovery": round(sum(recovery) / max(len(recovery), 1), 2) if recovery else None,
        "helped_rate_percent": round(helped / max(helped + false_alerts, 1) * 100, 1) if (helped or false_alerts) else None,
        "false_alert_rate_percent": round(false_alerts / max(helped + false_alerts, 1) * 100, 1) if (helped or false_alerts) else None,
        "privacy": {"raw_video_saved": False, "raw_audio_saved": False, "derived_signals_only": True},
    }


def write_daily_rollup(day: str | None = None) -> dict[str, Any]:
    ROLLUPS_DIR.mkdir(parents=True, exist_ok=True)
    rollup = _daily_rollup_for(day)
    atomic_io.atomic_write_json(ROLLUPS_DIR / f"{rollup['date']}.json", rollup)
    return {"ok": True, "rollup": rollup, "stored_at": _safe_relative(ROLLUPS_DIR / f"{rollup['date']}.json")}


def rollup_summary(days: int = 7) -> dict[str, Any]:
    ROLLUPS_DIR.mkdir(parents=True, exist_ok=True)
    today = _utc_now().date()
    rows = []
    for idx in range(max(1, days)):
        day = (today - timedelta(days=idx)).isoformat()
        payload = read_json(ROLLUPS_DIR / f"{day}.json") or (_daily_rollup_for(day) if idx == 0 else None)
        if payload:
            rows.append(payload)
    totals = {
        "sessions": sum(int(row.get("sessions") or 0) for row in rows),
        "signal_samples": sum(int(row.get("signal_samples") or 0) for row in rows),
        "feedback_records": sum(int(row.get("feedback_records") or 0) for row in rows),
        "labels": sum(int((row.get("labels") or {}).get("total") or 0) for row in rows),
    }
    return {"status": "ready", "days": days, "totals": totals, "rollups": rows}


_HOSTED_FAILURE_QUEUE = DATA_DIR / "hosted_storage_failures.jsonl"


def hosted_storage_status() -> dict[str, Any]:
    """Describe optional hosted storage without making local beta depend on it.

    Returns rich diagnostics so the /admin and /api/storage-health surfaces
    can tell the operator *why* the mirror is offline (env not set vs.
    half-configured vs. invalid URL).
    """
    provider = env_value("STORAGE_BACKEND", "local").lower()
    supabase_url = env_value("SUPABASE_URL")
    supabase_key = env_value("SUPABASE_SERVICE_ROLE_KEY")
    table = env_value("SUPABASE_EVENTS_TABLE", "kinaesthetic_events")
    timeout_ms = env_value("SUPABASE_TIMEOUT_MS", "1500")
    diagnostics: list[str] = []
    configured = False
    if provider != "supabase":
        diagnostics.append(f"STORAGE_BACKEND={provider!r}; set to 'supabase' to enable mirror")
    if not supabase_url:
        diagnostics.append("SUPABASE_URL is not set")
    elif not (supabase_url.startswith("https://") or supabase_url.startswith("http://")):
        diagnostics.append("SUPABASE_URL must start with https:// (or http:// for self-host)")
    if not supabase_key:
        diagnostics.append("SUPABASE_SERVICE_ROLE_KEY is not set")
    if not diagnostics and provider == "supabase":
        configured = True
    return {
        "provider": "supabase" if configured else "local_jsonl",
        "configured": configured,
        "table": table if configured else None,
        "timeout_ms": int(timeout_ms or "1500"),
        "diagnostics": diagnostics,
        "failure_queue": _safe_relative(_HOSTED_FAILURE_QUEUE),
        "failure_queue_count": count_lines(_HOSTED_FAILURE_QUEUE),
        "privacy": (
            "Only derived numeric signals, session events and labels are mirrored. "
            "Raw video, raw audio, IP addresses and emails are never uploaded."
        ),
    }


def _supabase_event_for(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return {
        "event_type": kind,
        "session_id": record.get("session_id") or payload.get("session_id"),
        "tester_id": record.get("tester_id") or payload.get("tester_id"),
        "game": record.get("game") or payload.get("game"),
        "payload": record,
        "created_at": record.get("timestamp") or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }


def mirror_to_hosted_storage(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    """Mirror derived public-beta records to Supabase Postgres (optional).

    Two design rules:
      * Local JSONL is the source of truth. Mirror failures must never
        block a live demo or camera session, so we swallow errors and
        return a structured status to the caller for ``/api/storage-health``.
      * If mirroring fails, we append the event to
        ``data/hosted_storage_failures.jsonl`` so it can be re-played
        offline by an operator (idempotent, append-only).
    """
    status = hosted_storage_status()
    if not status["configured"]:
        return {
            "mirrored": False,
            "provider": status["provider"],
            "reason": "not_configured",
            "diagnostics": status["diagnostics"],
        }

    supabase_url = env_value("SUPABASE_URL").rstrip("/")
    supabase_key = env_value("SUPABASE_SERVICE_ROLE_KEY")
    table = env_value("SUPABASE_EVENTS_TABLE", "kinaesthetic_events")
    timeout_seconds = max(0.3, status["timeout_ms"] / 1000.0)
    event = _supabase_event_for(kind, record)
    request = urllib.request.Request(
        f"{supabase_url}/rest/v1/{table}",
        data=json.dumps(event, ensure_ascii=False).encode("utf-8"),
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    last_error: str | None = None
    for attempt in range(2):  # one retry; we never want to block a frame
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                if 200 <= response.status < 300:
                    return {
                        "mirrored": True,
                        "provider": "supabase",
                        "status": response.status,
                        "attempts": attempt + 1,
                    }
                last_error = f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            # 4xx from Postgrest is almost always a schema/permission bug;
            # retrying won't fix it, so bail immediately.
            if 400 <= exc.code < 500:
                break
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.25 * (2 ** attempt))

    # Best-effort dead-letter so operators can replay later.
    try:
        atomic_io.atomic_append_jsonl(
            _HOSTED_FAILURE_QUEUE,
            {
                "queued_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "kind": kind,
                "error": (last_error or "unknown")[:240],
                "event": event,
            },
        )
    except OSError:
        pass
    return {
        "mirrored": False,
        "provider": "supabase",
        "error": (last_error or "unknown")[:240],
        "queued": True,
    }


def handle_public_session(payload: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(payload or {})
    action = str(payload.get("action") or "start").lower()[:32]
    if action not in {"start", "end", "ping", "heartbeat", "timeout"}:
        action = "ping"
    current = latest_public_session()
    requested_session_id = str(payload.get("session_id") or "").strip()
    session_id = requested_session_id or (public_session_id() if action == "start" else current.get("session_id") or public_session_id())
    now = _utc_now_iso()
    record = {
        "timestamp": now,
        "schema": "public_session_v1",
        "session_id": session_id,
        "action": action,
        "source": str(payload.get("source") or "browser_cv")[:64],
        "tester_id": public_tester_label(payload.get("tester_id") or "anonymous"),
        "game": str(payload.get("game") or "")[:80],
        "mode": str(payload.get("mode") or "browser")[:32],
        "privacy": {
            "raw_video_saved": False,
            "raw_audio_saved": False,
            "derived_signals_only": True,
        },
    }
    atomic_io.atomic_append_jsonl(PUBLIC_SESSIONS_PATH, record)
    state = {
        "session_id": session_id,
        "status": "ended" if action == "end" else "timeout" if action == "timeout" else "active",
        "started_at": current.get("started_at") if action != "start" else now,
        "updated_at": now,
        "ended_at": now if action in {"end", "timeout"} else None,
        "last_heartbeat_at": now if action in {"start", "heartbeat", "ping"} else current.get("last_heartbeat_at"),
        "source": record["source"],
        "mode": record["mode"],
        "tester_id": record["tester_id"],
        "game": record["game"],
        "last_summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else current.get("last_summary"),
    }
    atomic_io.atomic_write_json(PUBLIC_SESSION_STATE_PATH, state)
    storage = mirror_to_hosted_storage("public_session", record)
    write_daily_rollup()
    return {"ok": True, "session": state, "record": record, "storage": storage}


def public_session_heartbeat(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload or {})
    payload["action"] = "heartbeat"
    return handle_public_session(payload)


def expire_stale_public_session(timeout_seconds: float = SESSION_TIMEOUT_SECONDS) -> dict[str, Any]:
    current = latest_public_session()
    if not current or current.get("status") != "active":
        return {"ok": True, "expired": False, "session": current}
    last = parse_timestamp(current.get("last_heartbeat_at") or current.get("updated_at"))
    if not last:
        return {"ok": True, "expired": False, "session": current}
    age = (_utc_now() - last.astimezone(timezone.utc)).total_seconds()
    if age <= timeout_seconds:
        return {"ok": True, "expired": False, "age_seconds": round(age, 1), "session": current}
    result = handle_public_session(
        {
            "action": "timeout",
            "session_id": current.get("session_id"),
            "source": current.get("source") or "browser_cv",
            "mode": current.get("mode") or "browser",
            "tester_id": current.get("tester_id") or "anonymous",
            "game": current.get("game") or "",
            "summary": {"reason": "heartbeat_timeout", "age_seconds": round(age, 1)},
        }
    )
    result["expired"] = True
    result["age_seconds"] = round(age, 1)
    return result


def append_public_signal(payload: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(payload or {})
    current = latest_public_session()
    session_id = str(payload.get("session_id") or current.get("session_id") or public_session_id())
    if not current or current.get("session_id") != session_id or current.get("status") == "ended":
        handle_public_session({"action": "start", "session_id": session_id, "source": payload.get("source") or "browser_cv"})
        current = latest_public_session()
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    signals = sanitize_public_signal(payload)
    record = {
        "timestamp": now,
        "schema": "public_browser_signal_v1",
        "session_id": session_id,
        "source": str(payload.get("source") or "browser_cv")[:64],
        "tester_id": public_tester_label(payload.get("tester_id") or current.get("tester_id") or "anonymous"),
        "game": str(payload.get("game") or current.get("game") or "")[:80],
        "signals": signals,
        "privacy": {
            "raw_video_saved": False,
            "raw_audio_saved": False,
            "raw_frame_saved": False,
            "derived_signals_only": True,
        },
    }
    atomic_io.atomic_append_jsonl(PUBLIC_SIGNALS_PATH, record)
    state = dict(current)
    state.update({"session_id": session_id, "status": "active", "updated_at": now, "last_heartbeat_at": now, "last_signal": signals})
    profile_update = update_profile_from_calm_signal(record["tester_id"], signals)
    passive_label = maybe_harvest_passive_label(session_id, record["tester_id"], record["game"], signals, state)
    passive_memory = update_coach_memory_from_recovery(session_id, record["tester_id"], record["game"], signals, state)
    if passive_label.get("harvested"):
        state["last_passive_label_at"] = now
    atomic_io.atomic_write_json(PUBLIC_SESSION_STATE_PATH, state)
    storage = mirror_to_hosted_storage("public_signal", record)
    if count_lines(PUBLIC_SIGNALS_PATH) % 20 == 0:
        write_daily_rollup()
    return {"ok": True, "session_id": session_id, "stored_at": _safe_relative(PUBLIC_SIGNALS_PATH), "record": record, "storage": storage, "profile_update": profile_update, "passive_label": passive_label, "passive_memory": passive_memory}


def append_camera_check(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist camera readiness/failure metadata from /camera-check.

    This intentionally stores no images, frames, audio or video. It is for
    launch QA only: permission state, secure-context status, device counts and
    browser error names help us understand why real testers fail to start.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(payload or {})
    result = str(payload.get("result") or "unknown")[:32]
    if result not in {"ready", "blocked", "not_found", "busy", "timeout", "https_required", "unsupported", "error", "unknown"}:
        result = "unknown"
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    safe_diagnostics = {
        "is_secure_context": bool(diagnostics.get("is_secure_context")),
        "permission_state": str(diagnostics.get("permission_state") or "unknown")[:32],
        "media_devices_supported": bool(diagnostics.get("media_devices_supported")),
        "video_input_count": max(0, min(20, int(as_float(diagnostics.get("video_input_count"), 0)))),
        "audio_input_count": max(0, min(20, int(as_float(diagnostics.get("audio_input_count"), 0)))),
        "error_name": str(diagnostics.get("error_name") or "")[:80],
        "error_message": str(diagnostics.get("error_message") or "")[:200],
        "user_agent": str(diagnostics.get("user_agent") or "")[:220],
        "viewport": str(diagnostics.get("viewport") or "")[:64],
        "page": str(diagnostics.get("page") or "/camera-check")[:120],
    }
    record = {
        "timestamp": _utc_now_iso(),
        "schema": "camera_check_v1",
        "session_id": str(payload.get("session_id") or "")[:96],
        "tester_id": public_tester_label(payload.get("tester_id") or "anonymous"),
        "game": str(payload.get("game") or "")[:80],
        "source": str(payload.get("source") or "camera_check")[:64],
        "result": result,
        "diagnostics": safe_diagnostics,
        "privacy": {
            "raw_video_saved": False,
            "raw_audio_saved": False,
            "raw_frame_saved": False,
            "derived_signals_only": True,
        },
    }
    atomic_io.atomic_append_jsonl(CAMERA_CHECKS_PATH, record)
    storage = mirror_to_hosted_storage("camera_check", record)
    return {"ok": True, "stored_at": _safe_relative(CAMERA_CHECKS_PATH), "record": record, "storage": storage}


def public_session_summary(session_id: str | None = None) -> dict[str, Any]:
    current = latest_public_session()
    session_id = session_id or current.get("session_id")
    rows = tail_jsonl(PUBLIC_SIGNALS_PATH, 5000)
    if session_id:
        rows = [row for row in rows if row.get("session_id") == session_id]
    signals = [row.get("signals") or {} for row in rows]
    tilts = [as_float(row.get("tilt_risk")) for row in signals if row.get("tilt_risk") is not None]
    readiness = [as_float(row.get("readiness")) for row in signals if row.get("readiness") is not None]
    recovery = [as_float(row.get("recovery")) for row in signals if row.get("recovery") is not None]
    confidence = [as_float(row.get("signal_confidence")) for row in signals if row.get("signal_confidence") is not None]
    command_rows = [row for row in signals if row.get("recommendation")]

    peak = max(tilts) if tilts else 0.0
    final = tilts[-1] if tilts else 0.0
    peak_idx = tilts.index(peak) if tilts else 0
    post_peak_low = min(tilts[peak_idx:]) if tilts else final
    recovery_delta = max(0.0, peak - post_peak_low)
    dominant_lock = "jaw" if any(as_float(row.get("jaw_score")) >= 65 for row in signals) else "shoulders" if any(as_float(row.get("shoulder_score")) >= 65 for row in signals) else "mixed"
    proof_headline = "Недостаточно данных"
    if len(tilts) >= 5:
        proof_headline = f"Tilt {round(peak)} → {round(post_peak_low)}"
    elif tilts:
        proof_headline = "Собираем proof"
    return {
        "status": "ready" if rows else "empty",
        "session_id": session_id,
        "tester_id": current.get("tester_id"),
        "game": current.get("game"),
        "samples": len(rows),
        "started_at": current.get("started_at"),
        "updated_at": current.get("updated_at"),
        "ended_at": current.get("ended_at"),
        "max_tilt": round(peak, 1),
        "final_tilt": round(final, 1),
        "avg_readiness": round(sum(readiness) / max(len(readiness), 1), 1) if readiness else None,
        "avg_recovery": round(sum(recovery) / max(len(recovery), 1), 1) if recovery else None,
        "avg_confidence": round(sum(confidence) / max(len(confidence), 1), 2) if confidence else None,
        "recovery_delta": round(recovery_delta, 1),
        "dominant_lock": dominant_lock,
        "command_that_worked": command_rows[-1].get("recommendation") if command_rows else None,
        "proof": {
            "headline": proof_headline,
            "tilt_before": round(peak),
            "tilt_after": round(post_peak_low),
            "delta": round(recovery_delta, 1),
            "source": "browser_cv",
            "ready": len(tilts) >= 5,
        },
        "privacy": {"raw_video_saved": False, "raw_audio_saved": False, "derived_signals_only": True},
    }


def public_session_timeline(session_id: str | None = None, limit: int = 240) -> dict[str, Any]:
    """Return a compact derived-signal timeline for charts and replay."""
    current = latest_public_session()
    session_id = session_id or current.get("session_id")
    rows = tail_jsonl(PUBLIC_SIGNALS_PATH, 8000)
    if session_id:
        rows = [row for row in rows if row.get("session_id") == session_id]
    if limit > 0 and len(rows) > limit:
        step = max(1, math.ceil(len(rows) / limit))
        rows = rows[::step][-limit:]

    points: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    first_ts = parse_timestamp(rows[0].get("timestamp")) if rows else None
    last_command = ""
    high_open = False
    for idx, row in enumerate(rows):
        signals = row.get("signals") or {}
        ts = parse_timestamp(row.get("timestamp"))
        t = round((ts - first_ts).total_seconds(), 2) if ts and first_ts else idx
        tilt = round(as_float(signals.get("tilt_risk")), 1)
        point = {
            "t": t,
            "timestamp": row.get("timestamp"),
            "tilt_risk": tilt,
            "readiness": round(as_float(signals.get("readiness")), 1),
            "recovery": round(as_float(signals.get("recovery")), 1),
            "confidence": round(as_float(signals.get("signal_confidence")), 3),
            "jaw_tension": signals.get("jaw_tension"),
            "shoulder_tension": signals.get("shoulder_tension"),
        }
        points.append(point)
        recommendation = str(signals.get("recommendation") or "").strip()
        if recommendation and recommendation != last_command:
            events.append({"t": t, "type": "coach_command", "label": recommendation[:120]})
            last_command = recommendation
        if tilt >= 75 and not high_open:
            events.append({"t": t, "type": "tilt_rising", "label": "Риск тильта выше 75%"})
            high_open = True
        if high_open and tilt < 55:
            events.append({"t": t, "type": "recovery", "label": "Риск вернулся ниже 55%"})
            high_open = False

    return {
        "status": "ready" if points else "empty",
        "session_id": session_id,
        "points": points,
        "events": events[:80],
        "summary": public_session_summary(session_id),
        "privacy": {"raw_video_saved": False, "raw_audio_saved": False, "derived_signals_only": True},
    }


def validation_study_summary() -> dict[str, Any]:
    rows = tail_jsonl(VALIDATION_STUDY_PATH, 100_000)
    sessions = {}
    events = {}
    for row in rows:
        tester = row.get("tester_id") or "anonymous"
        sessions[tester] = sessions.get(tester, 0) + 1
        event = row.get("event") or "note"
        events[event] = events.get(event, 0) + 1
    return {
        "status": "ready",
        "events": len(rows),
        "unique_testers": len(sessions),
        "event_counts": events,
        "recent": rows[-12:][::-1],
        "target": {"testers": VALIDATION_TESTER_TARGET, "labels": VALIDATION_LABEL_TARGET},
        "cohort": cohort_summary(),
    }


def validation_mode_status(session_id: str | None = None) -> dict[str, Any]:
    summary = public_session_summary(session_id)
    cohort = cohort_summary()
    labels = feedback_rows_for_session(summary.get("session_id"))
    return {
        "status": "ready",
        "session_id": summary.get("session_id"),
        "tester_id": summary.get("tester_id"),
        "game": summary.get("game"),
        "session_samples": summary.get("samples", 0),
        "session_labels": len(labels),
        "target_minutes": 10,
        "target_labels": VALIDATION_LABEL_TARGET,
        "target_testers": VALIDATION_TESTER_TARGET,
        "cohort_progress_percent": cohort.get("progress_percent", 0),
        "labels_remaining": cohort.get("labels_remaining", VALIDATION_LABEL_TARGET),
        "message": f"Спасибо: эта сессия добавила {summary.get('samples', 0)} сигналов и {len(labels)} labels.",
    }


def share_proof(session_id: str | None = None) -> dict[str, Any]:
    score = public_session_score(session_id)
    before = score.get("max_tilt") or 0
    after = public_session_summary(score.get("session_id")).get("final_tilt") or 0
    delta = as_float(before) - as_float(after)
    return {
        "status": "ready" if score.get("samples") else "empty",
        "session_id": score.get("session_id"),
        "headline": f"Tilt {round(as_float(before))} → {round(as_float(after))}",
        "subline": (
            f"Recovery delta {round(delta, 1)} · {score.get('dominant_lock') or 'mixed'} lock"
            if score.get("samples") else "Запустите live demo, чтобы собрать proof."
        ),
        "stats": {
            "tilt_before": before,
            "tilt_after": after,
            "recovery_seconds": score.get("recovery_seconds"),
            "jaw": "Jaw tension ↓" if score.get("dominant_lock") == "jaw" else "Jaw stable",
            "shoulders": "Shoulders released" if score.get("dominant_lock") == "shoulders" else "Shoulders stable",
            "command_worked": bool(score.get("tester_confirmed_help")),
            "command": score.get("command_that_worked"),
        },
        "privacy": {"raw_video_saved": False, "raw_audio_saved": False},
    }


def investor_demo_script() -> dict[str, Any]:
    phases = [
        {"id": "baseline", "seconds": 15, "title": "Baseline", "tilt_risk": 28, "readiness": 82, "recovery": 76, "command": "Система строит личный baseline."},
        {"id": "rising_tilt", "seconds": 18, "title": "Rising tilt", "tilt_risk": 64, "readiness": 55, "recovery": 44, "command": "Риск растёт: челюсть и плечи напрягаются."},
        {"id": "alert", "seconds": 12, "title": "Alert", "tilt_risk": 84, "readiness": 38, "recovery": 27, "command": "Мягкая челюсть. Плечи вниз. Длинный выдох."},
        {"id": "recovery", "seconds": 25, "title": "Recovery", "tilt_risk": 46, "readiness": 70, "recovery": 68, "command": "Recovery improving. Сохраняй мягкость."},
        {"id": "proof", "seconds": 20, "title": "Proof card", "tilt_risk": 39, "readiness": 79, "recovery": 81, "command": "Tilt 84 → 39. Command worked."},
    ]
    return {
        "status": "ready",
        "total_seconds": sum(phase["seconds"] for phase in phases),
        "phases": phases,
        "export_endpoint": "/api/export-pitch-package",
        "message": "90-секундный сценарий для записи инвесторского ролика.",
    }


def evidence_summary() -> dict[str, Any]:
    public_summary = public_session_summary()
    timeline = public_session_timeline(public_summary.get("session_id"), limit=180)
    return {
        "status": "ready",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "public_session": public_summary,
        "public_timeline": timeline,
        "session_score": public_session_score(public_summary.get("session_id")),
        "coach_memory": coach_memory(public_summary.get("tester_id"), public_summary.get("game")),
        "proof_card": proof_card(),
        "session_report": session_report(),
        "investor_metrics": investor_metrics(),
        "cohort": cohort_summary(),
        "cloud_coach": llm_health(timeout=1.5),
        "hosted_storage": hosted_storage_status(),
        "privacy": privacy_passport(),
        "claim": "New category wedge: body-state anti-tilt coach for gamers. Early body-state signals, short performance commands and recovery proof. It is not medical diagnosis.",
        "next_milestone": {
            "labels_target": VALIDATION_LABEL_TARGET,
            "tester_target": VALIDATION_TESTER_TARGET,
            "goal": "Measure lead time before self-reported tilt, false alerts per hour, command help-rate and recovery delta.",
        },
    }


def sample_jsonl_row(path: Path) -> dict[str, Any] | None:
    rows = tail_jsonl(path, 1)
    return rows[-1] if rows else None


def data_room() -> dict[str, Any]:
    files = [
        (AFFECTIVE_DATASET_PATH, "affective_somatic_dataset_v1", "Derived body-state features, triggers and timestamps.", "auto/implicit body-state labels"),
        (EMBEDDINGS_PATH, "body_state_embedding_v1", "Vectorized body-state moments for similarity search.", "tokens/current phrase"),
        (FEEDBACK_PATH, "tester_feedback_v1", "Human validation labels from tester buttons and forms.", "felt_tension/helped/false_alert"),
        (PUBLIC_SIGNALS_PATH, "public_browser_signal_v1", "Browser-side CV derived signals from public demo sessions.", "implicit body-state signals"),
        (PUBLIC_SESSIONS_PATH, "public_session_v1", "Public web session start/end events.", "session lifecycle"),
        (CAMERA_CHECKS_PATH, "camera_check_v1", "Camera readiness checks and permission failure metadata.", "browser diagnostics"),
        (OVERLAY_STATE_PATH, "overlay_state_v1", "Latest live state consumed by site/OBS overlay.", "current state only"),
    ]
    rows = []
    for path, schema, description, labels in files:
        exists = path.exists()
        rows.append(
            {
                "file": _safe_relative(path),
                "exists": exists,
                "bytes": path.stat().st_size if exists else 0,
                "rows": count_lines(path) if path.suffix == ".jsonl" else (1 if exists else 0),
                "schema": schema,
                "description": description,
                "labels": labels,
                "contains_raw_video": False,
                "contains_raw_audio": False,
                "contains_sensitive_derived_signals": True,
                "example_row": sample_jsonl_row(path) if path.suffix == ".jsonl" else read_json(path),
            }
        )
    profile_files = sorted(PROFILES_DIR.glob("*.json")) if PROFILES_DIR.exists() else []
    rows.append(
        {
            "file": "data/profiles/*.json",
            "exists": bool(profile_files),
            "bytes": sum(path.stat().st_size for path in profile_files),
            "rows": len(profile_files),
            "schema": "personal_baseline_v2",
            "description": "Per-player neutral/jaw/shoulder/release baseline profiles.",
            "labels": "personal calibration phases",
            "contains_raw_video": False,
            "contains_raw_audio": False,
            "contains_sensitive_derived_signals": True,
            "example_row": read_json(profile_files[-1]) if profile_files else None,
        }
    )
    return {
        "status": "ready",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "raw_video_saved": False,
        "raw_audio_saved": False,
        "consent_version": "v1",
        "consent_audit_log": "data/consent_log.jsonl",
        "retention_days": 90,
        "delete_request_email": "privacy@kinaestheticai.com",
        "files": rows,
        "validation": cohort_summary(),
        "privacy_note": (
            "Only derived numeric scores, embeddings and optional tester labels are stored. "
            "Raw frames and microphone audio never leave the user's device. "
            "All write APIs require X-Kai-Token + X-Kai-Consent and are rate-limited per IP."
        ),
    }


def command_effectiveness() -> dict[str, Any]:
    """Honest effectiveness reporting.

    We deliberately do not return a 0..100 "effectiveness_score" any
    more — DD partners spotted that the previous formula clamped to 100
    on virtually any recovery and is therefore a vanity metric. We keep
    the raw ``tilt_delta`` (in tilt points) and ``recovery_seconds`` as
    the single source of truth.
    """
    overlay = read_overlay_state()
    samples = recent_samples(900)
    feature_rows = [sample.get("features") or {} for sample in samples]
    if not feature_rows:
        return {
            "status": "empty",
            "best_command": "Мягкая челюсть. Плечи вниз. Выдох.",
            "tilt_delta": 0.0,
            "recovery_seconds": None,
            "source": "demo",
            "message": "Недостаточно данных для command effectiveness.",
        }

    tilts = [as_float(row.get("tilt_score")) for row in feature_rows]
    summary = scoring.recovery_delta(tilts)
    delta = summary["delta"]
    recovery_seconds = (overlay.get("recovery") or {}).get("seconds")
    command = overlay.get("coach_alert") or "Мягкая челюсть. Плечи вниз. Выдох."
    has_real_peak = summary["status"] == "ready"
    return {
        "status": "ready" if has_real_peak else "no_peak",
        "best_command": command,
        "tilt_delta": delta,
        "tilt_before": summary["before"],
        "tilt_after": summary["after"],
        "recovery_seconds": recovery_seconds,
        "samples": len(feature_rows),
        "source": "real" if has_real_peak else "weak_signal",
        "message": (
            f"Команда снизила tilt на {delta:.1f} пунктов за "
            f"{recovery_seconds:.1f}с." if recovery_seconds and has_real_peak
            else f"Tilt дельта {delta:.1f} пунктов (по {len(feature_rows)} семплам)."
        ),
    }


def pitch_run_package() -> dict[str, Any]:
    package = export_founder_deck_package()
    run = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "session_report": session_report(),
        "proof_card": proof_card(),
        "command_effectiveness": command_effectiveness(),
        "privacy_passport": privacy_passport(),
        "llm_health": llm_health(timeout=1.5),
    }
    export_dir = Path(package["export_dir"])
    (export_dir / "pitch_run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    package["files"] = sorted(path.name for path in export_dir.iterdir())
    package["pitch_run"] = run
    return package


def session_report() -> dict[str, Any]:
    overlay = read_overlay_state()
    samples = recent_samples(800)
    feature_rows = [sample.get("features") or {} for sample in samples]

    if not feature_rows:
        return {
            "status": "empty",
            "summary": "Недостаточно данных для отчёта. Запустите live session.",
            "proof_card": proof_card(),
            "similar_states": similar_body_states(),
        }

    tilts = [as_float(row.get("tilt_score")) for row in feature_rows]
    jaws = [as_float(row.get("jaw_clench_score")) for row in feature_rows]
    shoulders = [as_float(row.get("shoulder_elevation_score")) for row in feature_rows]
    readiness = [feature_readiness(row) for row in feature_rows]

    peak_idx = max(range(len(tilts)), key=lambda idx: tilts[idx])
    peak_tilt = tilts[peak_idx]
    final_tilt = tilts[-1]
    post_peak_low = min(tilts[peak_idx:]) if peak_idx < len(tilts) else final_tilt
    triggers: dict[str, int] = {}
    for sample in samples:
        for trigger in sample.get("triggers") or []:
            triggers[trigger] = triggers.get(trigger, 0) + 1

    dominant_trigger = max(triggers.items(), key=lambda item: item[1])[0] if triggers else "stable_control"
    first_pattern = None
    for sample, features in zip(samples, feature_rows):
        if (
            as_float(features.get("tilt_score")) >= 65
            or as_float(features.get("jaw_clench_score")) >= 70
            or as_float(features.get("shoulder_elevation_score")) >= 65
        ):
            first_pattern = sample.get("timestamp")
            break

    command = overlay.get("coach_alert") or "Локальная команда: мягкая челюсть, плечи вниз, длинный выдох."
    recovery_seconds = (overlay.get("recovery") or {}).get("seconds")

    return {
        "status": "ready",
        "session_id": overlay.get("session_id") or (samples[-1].get("session_id") if samples else None),
        "samples": len(samples),
        "summary": "Сессия показывает связку body-state -> intervention -> recovery proof.",
        "what_triggered_tilt": dominant_trigger,
        "pattern_started_at": first_pattern,
        "peak_tilt": round(peak_tilt, 1),
        "final_tilt": round(final_tilt, 1),
        "avg_readiness": round(sum(readiness) / max(len(readiness), 1), 1),
        "recovery_delta": round(max(peak_tilt - post_peak_low, 0.0), 1),
        "recovery_seconds": recovery_seconds,
        "command_that_worked": command,
        "personal_somatic_pattern": (overlay.get("fingerprint") or {}).get("dominant_pattern", dominant_trigger),
        "jaw_peak": round(max(jaws), 1),
        "jaw_final": round(jaws[-1], 1),
        "shoulder_peak": round(max(shoulders), 1),
        "shoulder_final": round(shoulders[-1], 1),
        "similar_states": similar_body_states(),
        "proof_card": proof_card(feature_rows=feature_rows, overlay=overlay),
    }


def proof_card(
    feature_rows: list[dict[str, Any]] | None = None,
    overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Before/after recovery card.

    Always includes ``source`` (``real`` | ``demo``) and ``is_synthetic``
    so downstream investor surfaces (`/metrics`, founder export) can
    display only verified proof rather than the synthetic placeholder.
    """
    overlay = overlay or read_overlay_state()
    if feature_rows is None:
        feature_rows = [sample.get("features") or {} for sample in recent_samples(800)]
    if not feature_rows:
        return {
            "status": "demo",
            "source": "demo",
            "is_synthetic": True,
            "headline": "Tilt 84 → 39 за 12 сек (демо)",
            "tilt_before": 84,
            "tilt_after": 39,
            "recovery_seconds": 12,
            "jaw": "Jaw lock ↓",
            "shoulders": "Shoulders released",
            "command": "Мягкая челюсть. Плечи вниз. Выдох.",
        }

    tilts = [as_float(row.get("tilt_score")) for row in feature_rows]
    jaws = [as_float(row.get("jaw_clench_score")) for row in feature_rows]
    shoulders = [as_float(row.get("shoulder_elevation_score")) for row in feature_rows]
    summary = scoring.recovery_delta(tilts, min_peak=45.0)
    before = summary["before"]
    after = summary["after"]
    recovery_seconds = (overlay.get("recovery") or {}).get("seconds")
    has_real_peak = summary["status"] == "ready"
    is_synthetic = not has_real_peak

    return {
        "status": "ready" if has_real_peak else "no_peak",
        "source": "real" if has_real_peak else "weak_signal",
        "is_synthetic": is_synthetic,
        "headline": (
            f"Tilt {before:g} → {after:g}"
            + (f" за {float(recovery_seconds):g} сек" if recovery_seconds else "")
            + ("" if has_real_peak else " · слабый сигнал")
        ),
        "tilt_before": before,
        "tilt_after": after,
        "tilt_delta": summary["delta"],
        "recovery_seconds": recovery_seconds,
        "samples": len(feature_rows),
        "jaw": "Jaw lock ↓" if max(jaws) - jaws[-1] >= 12 else "Jaw stable",
        "shoulders": "Shoulders released" if max(shoulders) - shoulders[-1] >= 12 else "Shoulders stable",
        "command": overlay.get("coach_alert") or "Мягкая челюсть. Плечи вниз. Выдох.",
    }


def privacy_passport() -> dict[str, Any]:
    data_files = []
    for path in [OVERLAY_STATE_PATH, AFFECTIVE_DATASET_PATH, EMBEDDINGS_PATH, DATA_DIR / "somatic_stream.csv"]:
        data_files.append(
            {
                "file": _safe_relative(path),
                "exists": path.exists(),
                "bytes": path.stat().st_size if path.exists() else 0,
                "contains_raw_video": False,
                "contains_audio_recording": False,
                "contains_sensitive_derived_signals": True,
            }
        )
    return {
        "raw_video_saved": False,
        "raw_audio_saved": False,
        "processing_model": "local-first",
        "consent_version": "v1",
        "consent_audit_log": "data/consent_log.jsonl",
        "what_is_stored": [
            "Numeric tilt / readiness / recovery / jaw / shoulder scores per frame",
            "Coach commands shown to the user (not your audio response)",
            "Session boundaries and tester pseudonym",
            "Self-reported labels (helped / felt_tension / false_alert) when you press T/H/F",
        ],
        "what_is_not_stored_by_default": [
            "Raw webcam video frames (kept in browser / local engine memory only)",
            "Raw audio (microphone is not used)",
            "Email or real name (we use a tester_id you choose)",
            "IP address in cleartext datasets (only IP-derived rate-limit buckets in process memory)",
        ],
        "user_controls": [
            "Decline consent dialog -> nothing is recorded server-side",
            "Email privacy@kinaestheticai.com with your tester_id to delete within 7 business days",
            "Disable voice/input telemetry from the cockpit advanced panel",
            "Delete local data/ folder to wipe the desktop install",
        ],
        "retention_days": 90,
        "delete_request_email": "privacy@kinaestheticai.com",
        "legal_basis": "GDPR Art. 6(1)(a) consent; Art. 9(2)(a) for derived biometric signals where applicable",
        "data_files": data_files,
        "medical_claims": False,
        "message": "Kinaesthetic AI is performance coaching, not diagnosis or treatment.",
    }


def llm_health(timeout: float = 2.5) -> dict[str, Any]:
    return coach_providers.health(timeout=timeout)


def generate_coach_response(payload: dict[str, Any], timeout: float = 8.0) -> dict[str, Any]:
    """Generate one short coach command through Groq/Gemini with local fallback."""
    payload = payload or {}
    session_key = str(payload.get("session_id") or payload.get("tester_id") or "global")[:96]
    now = time.monotonic()
    cached = _coach_cache.get(session_key)
    if cached and now - float(cached.get("created_monotonic", 0.0)) < COACH_RATE_LIMIT_SECONDS:
        response = dict(cached.get("response") or {})
        response["cached"] = True
        response["rate_limited_seconds"] = COACH_RATE_LIMIT_SECONDS
        return response
    response = coach_providers.generate_coach_command(payload, timeout=timeout)
    response["cached"] = False
    response["rate_limited_seconds"] = COACH_RATE_LIMIT_SECONDS
    _coach_cache[session_key] = {"created_monotonic": now, "response": response}
    if len(_coach_cache) > 256:
        oldest = sorted(_coach_cache.items(), key=lambda item: float(item[1].get("created_monotonic", 0.0)))[:64]
        for key, _ in oldest:
            _coach_cache.pop(key, None)
    return response


def append_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a tester feedback record.

    Writes through ``atomic_io`` so a crash mid-write never produces a
    partial JSON line. We also normalise the three core ground-truth
    flags into booleans so downstream aggregation is deterministic.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(payload or {})
    for key in ("felt_tension", "helped", "false_alert"):
        payload[key] = bool(payload.get(key))
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "schema": "tester_feedback_v1",
        "payload": payload,
    }
    atomic_io.atomic_append_jsonl(FEEDBACK_PATH, record)
    update_coach_memory_from_feedback(record)
    storage = mirror_to_hosted_storage("tester_feedback", record)
    write_daily_rollup()
    return {"ok": True, "stored_at": _safe_relative(FEEDBACK_PATH), "record": record, "storage": storage}


def _policy_metrics(window_days: int = 7) -> dict[str, Any]:
    summary = rollup_summary(window_days)
    rows = summary.get("rollups") or []
    helped = sum(int((row.get("labels") or {}).get("helped") or 0) for row in rows)
    false_alerts = sum(int((row.get("labels") or {}).get("false_alert") or 0) for row in rows)
    labels = sum(int((row.get("labels") or {}).get("total") or 0) for row in rows)
    signal_samples = sum(int(row.get("signal_samples") or 0) for row in rows)
    helped_rate = helped / max(helped + false_alerts, 1)
    false_alert_rate = false_alerts / max(helped + false_alerts, 1)
    return {
        "window_days": window_days,
        "labels": labels,
        "helped": helped,
        "false_alerts": false_alerts,
        "signal_samples": signal_samples,
        "helped_rate": round(helped_rate, 4),
        "false_alert_rate": round(false_alert_rate, 4),
    }


def _default_policy() -> dict[str, Any]:
    return {
        "schema": "somatic_policy_v1",
        "version": "policy_v0",
        "created_at": _utc_now_iso(),
        "status": "baseline",
        "canary_percent": 0,
        "weights": {
            "face": 0.34,
            "posture": 0.30,
            "shoulders": 0.18,
            "motion": 0.10,
            "recovery": 0.08,
        },
        "thresholds": {
            "alert_tilt": 72,
            "warning_tilt": 45,
            "confidence_floor": 0.45,
            "calm_tilt_max": 35,
        },
        "metrics": _policy_metrics(),
        "privacy": {"raw_video_saved": False, "raw_audio_saved": False, "derived_signals_only": True},
    }


def active_policy() -> dict[str, Any]:
    policy = read_json(ACTIVE_POLICY_PATH)
    if policy:
        return policy
    policy = _default_policy()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_io.atomic_write_json(ACTIVE_POLICY_PATH, policy)
    return policy


def train_candidate_policy(window_days: int = 7) -> dict[str, Any]:
    metrics = _policy_metrics(window_days)
    current = active_policy()
    weights = dict(current.get("weights") or {})
    thresholds = dict(current.get("thresholds") or {})
    helped_rate = float(metrics.get("helped_rate") or 0.0)
    false_alert_rate = float(metrics.get("false_alert_rate") or 0.0)

    # Conservative local adaptation: no model weights are promoted blindly.
    # We only nudge thresholds from derived labels, then send the candidate
    # through an evaluation gate.
    if metrics.get("labels", 0) >= 5:
        if false_alert_rate > 0.35:
            thresholds["alert_tilt"] = min(82, float(thresholds.get("alert_tilt", 72)) + 3)
            thresholds["warning_tilt"] = min(55, float(thresholds.get("warning_tilt", 45)) + 2)
        elif helped_rate >= 0.65 and false_alert_rate <= 0.25:
            thresholds["alert_tilt"] = max(66, float(thresholds.get("alert_tilt", 72)) - 2)
            thresholds["warning_tilt"] = max(38, float(thresholds.get("warning_tilt", 45)) - 1)
            weights["recovery"] = min(0.14, float(weights.get("recovery", 0.08)) + 0.01)

    version = f"policy_v{_utc_now().strftime('%Y%m%d_%H%M%S')}"
    return {
        "schema": "somatic_policy_v1",
        "version": version,
        "created_at": _utc_now_iso(),
        "status": "candidate",
        "canary_percent": 10,
        "weights": weights,
        "thresholds": thresholds,
        "metrics": metrics,
        "trained_from": {
            "window_days": window_days,
            "signal_file": _safe_relative(PUBLIC_SIGNALS_PATH),
            "feedback_file": _safe_relative(FEEDBACK_PATH),
            "raw_video_saved": False,
            "raw_audio_saved": False,
        },
    }


def evaluate_policy_candidate(candidate: dict[str, Any], tolerance: float = 0.08) -> dict[str, Any]:
    current = active_policy()
    before = current.get("metrics") or _policy_metrics()
    after = candidate.get("metrics") or _policy_metrics()
    enough_data = int(after.get("labels") or 0) >= int(env_value("AUTOPROMOTE_MIN_SESSIONS", "5") or "5")
    helped_ok = float(after.get("helped_rate") or 0.0) >= float(before.get("helped_rate") or 0.0)
    false_alert_ok = float(after.get("false_alert_rate") or 0.0) <= float(before.get("false_alert_rate") or 0.0) + tolerance
    return {
        "ok": enough_data and helped_ok and false_alert_ok,
        "enough_data": enough_data,
        "helped_ok": helped_ok,
        "false_alert_ok": false_alert_ok,
        "before": before,
        "after": after,
        "tolerance": tolerance,
    }


def run_autolearn_once(force: bool = False) -> dict[str, Any]:
    enabled = env_value("AUTOLEARN_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    if not enabled and not force:
        return {"ok": True, "enabled": False, "message": "AUTOLEARN_ENABLED=0; safe local learning is idle."}
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    write_daily_rollup()
    flush = flush_write_queue(limit=500)
    candidate = train_candidate_policy(window_days=7)
    evaluation = evaluate_policy_candidate(candidate)
    candidate_path = MODELS_DIR / f"{candidate['version']}.json"
    atomic_io.atomic_write_json(candidate_path, candidate)
    promoted = False
    if evaluation.get("ok"):
        previous = active_policy()
        candidate["status"] = "active"
        candidate["rollback_pointer"] = previous.get("version")
        atomic_io.atomic_write_json(ACTIVE_POLICY_PATH, candidate)
        promoted = True
    lineage = {
        "timestamp": _utc_now_iso(),
        "candidate_version": candidate["version"],
        "candidate_path": _safe_relative(candidate_path),
        "promoted": promoted,
        "evaluation": evaluation,
        "queue_flush": flush,
    }
    atomic_io.atomic_append_jsonl(POLICY_LINEAGE_PATH, lineage)
    atomic_io.atomic_write_json(
        AUTOLEARN_STATE_PATH,
        {
            "updated_at": _utc_now_iso(),
            "enabled": enabled,
            "last_candidate": candidate["version"],
            "last_promoted": candidate["version"] if promoted else active_policy().get("version"),
            "evaluation": evaluation,
            "lineage_path": _safe_relative(POLICY_LINEAGE_PATH),
        },
    )
    return {"ok": True, "enabled": enabled, "promoted": promoted, "candidate": candidate, "evaluation": evaluation, "queue_flush": flush}


def autolearn_status() -> dict[str, Any]:
    return {
        "status": "ready",
        "enabled": env_value("AUTOLEARN_ENABLED", "0").lower() in {"1", "true", "yes", "on"},
        "interval_min": int(env_value("AUTOLEARN_INTERVAL_MIN", "15") or "15"),
        "autopromote_min_sessions": int(env_value("AUTOPROMOTE_MIN_SESSIONS", "5") or "5"),
        "active_policy": active_policy(),
        "state": read_json(AUTOLEARN_STATE_PATH) or {},
        "queue_count": count_lines(WRITE_QUEUE_PATH),
        "rollups": rollup_summary(),
        "privacy": {"raw_video_saved": False, "raw_audio_saved": False, "derived_signals_only": True},
    }


def export_founder_deck_package() -> dict[str, Any]:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    export_dir = EXPORTS_DIR / f"pitch_package_{stamp}"
    export_dir.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "evidence_summary.json": evidence_summary(),
        "public_session_report.json": public_session_summary(),
        "public_session_timeline.json": public_session_timeline(),
        "session_report.json": session_report(),
        "session_replay.json": session_replay(),
        "proof_card.json": proof_card(),
        "cohort_summary.json": cohort_summary(),
        "validation_study_summary.json": validation_study_summary(),
        "command_effectiveness_table.json": command_effectiveness_table(),
        "coach_command_library.json": coach_command_library(),
        "false_alert_review.json": false_alert_review(),
        "data_room.json": data_room(),
        "privacy_passport.json": privacy_passport(),
        "llm_health.json": llm_health(timeout=1.5),
    }
    for name, payload in artifacts.items():
        atomic_io.atomic_write_json(export_dir / name, payload)
    (export_dir / "README.md").write_text(
        "# Kinaesthetic AI Founder Deck Export\n\n"
        "Сюда собраны производные метрики для pitch evidence. Raw video/audio не экспортируются.\n\n"
        "- session_report.json: session intelligence\n"
        "- session_replay.json: tilt timeline for screenshots\n"
        "- proof_card.json: before/after proof\n"
        "- cohort_summary.json: validation labels and help/false-alert rates\n"
        "- validation_study_summary.json: 20-50 player study progress\n"
        "- command_effectiveness_table.json: which commands are working\n"
        "- data_room.json: local data schemas and examples\n"
        "- privacy_passport.json: privacy/data ownership summary\n"
        "- llm_health.json: текущий статус LLM endpoint\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "export_dir": str(export_dir),
        "files": sorted(path.name for path in export_dir.iterdir()),
    }
