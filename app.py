from force_utf8 import force_utf8

force_utf8()

import csv
import hashlib
import html
import importlib.util
import json
import math
import os
import shutil
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import mediapipe as mp
import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from mediapipe.tasks.python import vision
from openai import OpenAI

import alerts
import atomic_io
import coach_providers
import cv_engine
import gemini_coach
import scoring

try:
    import product_intelligence
except Exception:
    product_intelligence = None

try:
    import engine_status
except Exception:
    engine_status = None

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    from pynput import keyboard as pynput_keyboard
    from pynput import mouse as pynput_mouse
except Exception:
    pynput_keyboard = None
    pynput_mouse = None


load_dotenv()


APP_ROOT = Path(__file__).resolve().parent
MODELS_DIR = APP_ROOT / "models"
DATA_DIR = APP_ROOT / "data"
SOMATIC_LOG_PATH = DATA_DIR / "somatic_stream.csv"
AFFECTIVE_DATASET_PATH = DATA_DIR / "affective_somatic_dataset.jsonl"
SOMATIC_MODEL_PATH = DATA_DIR / "somatic_proto_model.json"
PROFILES_DIR = DATA_DIR / "profiles"
REPORTS_DIR = DATA_DIR / "reports"
BODY_LANGUAGE_API_PATH = DATA_DIR / "body_language_api_stream.jsonl"
BODY_STATE_EMBEDDINGS_PATH = DATA_DIR / "body_state_embeddings.jsonl"
EXTERNAL_EVENTS_INBOX_PATH = DATA_DIR / "external_game_events.jsonl"
OVERLAY_STATE_PATH = DATA_DIR / "overlay_state.json"

POSE_MODEL_URL = cv_engine.POSE_MODEL_URL
FACE_MODEL_URL = cv_engine.FACE_MODEL_URL
POSE_MODEL_PATH = MODELS_DIR / "pose_landmarker_lite.task"
FACE_MODEL_PATH = MODELS_DIR / "face_landmarker.task"

LLM_SYSTEM_PROMPT = (
    "You are an esports performance coach with biomechanics expertise. "
    "The player is close to tilt and shoulder/jaw tension is detected. "
    "Return exactly ONE short command, maximum 10 words. "
    "No markdown, no list, no explanation, no medical claims."
)

COACH_MODE_PROMPTS = {
    "Drill coach": "Style: sharp drill-sergeant command.",
    "Calm coach": "Style: calm, immediate, non-distracting command.",
    "Pro coach": "Style: concise competitive esports language.",
    "Biomechanics": "Style: body-based cue in gamer language, no lecture.",
}


def sanitize_coach_command(text: str) -> str:
    """Keep every coach output to one short cue."""
    value = str(text or "").strip()
    if not value:
        return "SOFTEN JAW. DROP SHOULDERS."
    value = value.replace("\r", "\n")
    lines = [line.strip(" \t-*0123456789.)") for line in value.split("\n") if line.strip()]
    value = lines[0] if lines else value
    for sep in (";", " then ", " and then ", " Next ", " Also "):
        if sep in value:
            value = value.split(sep, 1)[0].strip()
    words = value.split()
    if len(words) > 10:
        value = " ".join(words[:10])
    return value.strip(" ,.!?:;-").upper() or "SOFTEN JAW. DROP SHOULDERS."

@dataclass
class SimpleLandmark:
    x: float
    y: float
    z: float = 0.0
    visibility: float = 1.0
    presence: float = 1.0


@dataclass
class BiomechanicsMetrics:
    timestamp: str
    nose_x: float
    nose_y: float
    left_shoulder_x: float
    left_shoulder_y: float
    right_shoulder_x: float
    right_shoulder_y: float
    shoulder_width_px: float
    nose_to_shoulders_ratio: float
    shoulder_elevation_score: float
    shoulder_asymmetry_percent: float
    shoulder_asymmetry_score: float
    shoulder_slope_degrees: float
    jaw_open_ratio: Optional[float]
    jaw_blendshape_score: float
    jaw_clench_score: float
    brow_tension_score: float
    eye_focus_score: float
    mouth_tension_score: float
    emotional_arousal_score: float
    emotional_valence_score: float
    emotion_primary: str
    emotion_confidence: float
    emotion_scores: dict[str, float]
    raw_stress_score: float
    tilt_score: float
    triggers: list[str]
    # Posture composite (forward head, rolled shoulders, off-center torso).
    # Defaulted so older construction sites and tests stay valid.
    forward_head_score: float = 0.0
    shoulder_protraction_score: float = 0.0
    posture_stress_score: float = 0.0
    dominant_posture_channel: str = "neutral"
    dominant_posture_value: float = 0.0


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


class VoiceTelemetry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stream = None
        self.started = False
        self.status = "Voice layer off"
        self.voice_events = deque(maxlen=90)
        self.metrics = {
            "voice_rms": 0.0,
            "voice_pitch_proxy": 0.0,
            "voice_tension_score": 0.0,
            "speech_rate_proxy": 0.0,
            "voice_status": self.status,
        }

    def start(self) -> None:
        if self.started:
            return
        if sd is None:
            self.status = "sounddevice is not installed: pip install sounddevice"
            self.metrics["voice_status"] = self.status
            return
        try:
            self.stream = sd.InputStream(
                channels=1,
                samplerate=16000,
                blocksize=1600,
                callback=self._callback,
            )
            self.stream.start()
            self.started = True
            self.status = "Voice layer active"
        except Exception as exc:
            self.status = f"Microphone unavailable: {exc}"
        self.metrics["voice_status"] = self.status

    def stop(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        self.stream = None
        self.started = False
        self.status = "Voice layer off"
        self.metrics["voice_status"] = self.status

    def _callback(self, indata, frames, timestamp, status) -> None:
        audio = np.asarray(indata[:, 0], dtype=np.float32)
        rms = float(np.sqrt(np.mean(np.square(audio))) + 1e-8)
        speaking = rms > 0.012
        if speaking:
            self.voice_events.append(time.time())

        zero_crossings = np.mean(np.abs(np.diff(np.signbit(audio))).astype(float))
        spectrum = np.abs(np.fft.rfft(audio))
        freqs = np.fft.rfftfreq(len(audio), d=1.0 / 16000)
        centroid = float(np.sum(freqs * spectrum) / max(np.sum(spectrum), 1e-8))
        pitch_proxy = clamp((centroid - 450) / 2100 * 100)
        loudness = clamp((rms - 0.006) / 0.055 * 100)
        pressed_voice = clamp((zero_crossings - 0.055) / 0.11 * 100)
        tension = clamp(loudness * 0.34 + pitch_proxy * 0.38 + pressed_voice * 0.28)

        now = time.time()
        recent_events = [event for event in self.voice_events if now - event <= 8]
        speech_rate = clamp(len(recent_events) / 8 * 70)
        with self.lock:
            self.metrics = {
                "voice_rms": round(loudness, 2),
                "voice_pitch_proxy": round(pitch_proxy, 2),
                "voice_tension_score": round(tension, 2),
                "speech_rate_proxy": round(speech_rate, 2),
                "voice_status": self.status,
            }

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.metrics)


class InputTelemetry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started = False
        self.status = "Mouse/keyboard telemetry off"
        self.key_events = deque(maxlen=500)
        self.click_events = deque(maxlen=500)
        self.move_events = deque(maxlen=800)
        self.last_pos = None
        self.keyboard_listener = None
        self.mouse_listener = None

    def start(self) -> None:
        if self.started:
            return
        if pynput_keyboard is None or pynput_mouse is None:
            self.status = "pynput is not installed: pip install pynput"
            return
        try:
            self.keyboard_listener = pynput_keyboard.Listener(on_press=self._on_key_press)
            self.mouse_listener = pynput_mouse.Listener(
                on_move=self._on_move,
                on_click=self._on_click,
            )
            self.keyboard_listener.start()
            self.mouse_listener.start()
            self.started = True
            self.status = "Mouse/keyboard telemetry active"
        except Exception as exc:
            self.status = f"Input telemetry unavailable: {exc}"

    def stop(self) -> None:
        for listener in (self.keyboard_listener, self.mouse_listener):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        self.keyboard_listener = None
        self.mouse_listener = None
        self.started = False
        self.status = "Mouse/keyboard telemetry off"

    def _on_key_press(self, key) -> None:
        with self.lock:
            self.key_events.append(time.time())

    def _on_click(self, x, y, button, pressed) -> None:
        if not pressed:
            return
        with self.lock:
            self.click_events.append(time.time())

    def _on_move(self, x, y) -> None:
        now = time.time()
        with self.lock:
            if self.last_pos is not None:
                last_x, last_y, last_t = self.last_pos
                dt = max(now - last_t, 0.001)
                distance = math.hypot(x - last_x, y - last_y)
                self.move_events.append((now, distance / dt))
            self.last_pos = (x, y, now)

    def snapshot(self) -> dict:
        now = time.time()
        with self.lock:
            keys = [event for event in self.key_events if now - event <= 5]
            clicks = [event for event in self.click_events if now - event <= 5]
            moves = [speed for event, speed in self.move_events if now - event <= 5]
        key_rate = len(keys) / 5
        click_rate = len(clicks) / 5
        avg_speed = float(np.mean(moves)) if moves else 0.0
        chaos = clamp(key_rate * 12 + click_rate * 22 + min(avg_speed / 2600 * 100, 100) * 0.32)
        return {
            "key_rate_5s": round(key_rate, 2),
            "click_rate_5s": round(click_rate, 2),
            "mouse_speed_proxy": round(clamp(avg_speed / 2600 * 100), 2),
            "input_chaos_score": round(chaos, 2),
            "input_status": self.status,
        }


@st.cache_resource
def get_voice_telemetry() -> VoiceTelemetry:
    return VoiceTelemetry()


@st.cache_resource
def get_input_telemetry() -> InputTelemetry:
    return InputTelemetry()


def init_session_state() -> None:
    defaults = {
        "running": False,
        "camera": None,
        "smoothed_pose_landmarks": None,
        "smoothed_face_landmarks": None,
        "last_face_landmarks": None,
        "last_face_blendshapes": {},
        "frame_counter": 0,
        "last_frame_ts_ms": 0,
        "last_tilt_update_at": 0.0,
        "last_log_write_at": 0.0,
        "last_dataset_write_at": 0.0,
        "last_api_event_write_at": 0.0,
        "last_embedding_write_at": 0.0,
        "tilt_score": 0.0,
        "shoulder_elevated_seconds": 0.0,
        "asymmetry_seconds": 0.0,
        "jaw_clench_seconds": 0.0,
        "emotional_tension_seconds": 0.0,
        "coach_alert": "",
        "use_llm_alerts": False,
        "last_local_alert_at": 0.0,
        "last_llm_call_at": 0.0,
        "last_recovery_update_at": 0.0,
        "last_autopilot_at": 0.0,
        "autopilot_enabled": True,
        "autopilot_status": "Autopilot waiting for signal",
        "autopilot_cue": "",
        "recovery_active": False,
        "recovery_started_at": 0.0,
        "recovery_start_tilt": 0.0,
        "recovery_score": 0.0,
        "recovery_seconds": None,
        "llm_future": None,
        "llm_pending_started_at": 0.0,
        "somatic_logs": deque(maxlen=18),
        "session_series": deque(maxlen=3600),
        "game_events": deque(maxlen=24),
        "session_id": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "is_calibrating": False,
        "calibration_started_at": 0.0,
        "calibration_duration": 10.0,
        "calibration_samples": [],
        "baseline_profile": None,
        "last_game_event": "",
        "overlay_mode": False,
        "landmark_only_logging": True,
        "fast_mode": True,
        "camera_width": 640,
        "camera_height": 360,
        "effective_camera_width": 640,
        "effective_camera_height": 360,
        "effective_camera_fps": 15,
        "coach_mode": "Drill coach",
        "tilt_prediction": {
            "seconds": None,
            "confidence": 0.0,
            "slope_per_sec": 0.0,
            "label": "collecting signal",
        },
        "somatic_fingerprint": None,
        "post_match_report": None,
        "last_exported_report_path": "",
        "last_founder_deck_path": "",
        "external_event_offset": 0,
        "creator_viewer_resets": 0,
        "party_members": {},
        "current_metrics": None,
        "face_tracking_quality": 0.0,
        "jaw_relaxed_baseline": None,
        "jaw_clenched_baseline": None,
        "jaw_calibration_samples": [],
        "jaw_calibration_phase": "",
        "face_status": "Face Mesh waiting for frame",
        "pipeline_status": "Waiting for camera start",
        "auto_dataset_enabled": True,
        "dataset_samples_written": 0,
        "somatic_model": None,
        "somatic_model_status": "Proto-model is not trained yet",
        "somatic_model_prediction": None,
        "openface_status": "OpenFace not checked",
        "openface_executable": "",
        "emotion_backend": "Realtime MediaPipe: 478 points + 52 blendshapes",
        "show_face_points_overlay": True,
        "guided_ui": True,
        "enable_voice_layer": False,
        "enable_input_telemetry": False,
        "voice_metrics": {},
        "input_metrics": {},
        "user_profile_id": "default_player",
        "profile_status": "Personal profile not loaded",
        "somatic_twin_memory": None,
        "somatic_twin_status": "Somatic Twin Memory has not updated yet",
        "next_best_intervention": None,
        "ui_mode": "Pitch cockpit",
        "somatic_search_query": "jaw lock after death",
        "somatic_search_results": [],
        "copilot_question": "",
        "copilot_answer": "Ask a session question: why did breakdown happen, what should I do before clutch, which command worked.",
        "performance_profile": {
            "fps": 0.0,
            "capture_ms": 0.0,
            "pose_ms": 0.0,
            "face_ms": 0.0,
            "frame_ms": 0.0,
        },
        "last_perf_frame_at": 0.0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def ensure_model(model_path: Path, model_url: str) -> None:
    cv_engine.ensure_model(model_path, model_url)


@st.cache_resource
def get_pose_detector():
    return cv_engine.create_pose_detector(POSE_MODEL_PATH)


@st.cache_resource
def get_face_detector():
    try:
        return cv_engine.create_face_detector(FACE_MODEL_PATH)
    except Exception:
        return None


@st.cache_resource
def get_llm_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=1)


def safe_float(value, fallback: float = 1.0) -> float:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def to_simple_landmark(landmark) -> SimpleLandmark:
    return SimpleLandmark(
        x=safe_float(getattr(landmark, "x", 0.0), 0.0),
        y=safe_float(getattr(landmark, "y", 0.0), 0.0),
        z=safe_float(getattr(landmark, "z", 0.0), 0.0),
        # Legacy implementation note cleaned for English demo.
        # Legacy implementation note cleaned for English demo.
        visibility=safe_float(getattr(landmark, "visibility", 1.0), 1.0),
        presence=safe_float(getattr(landmark, "presence", 1.0), 1.0),
    )


def smooth_landmarks(
    raw_landmarks,
    state_key: str,
    frame_width: int,
    frame_height: int,
    alpha: float,
    dead_zone_px: float,
) -> list[SimpleLandmark]:
    previous = st.session_state.get(state_key)
    current_landmarks = [to_simple_landmark(landmark) for landmark in raw_landmarks]

    if previous is None or len(previous) != len(current_landmarks):
        st.session_state[state_key] = current_landmarks
        return current_landmarks

    smoothed = []
    for previous_landmark, current_landmark in zip(previous, current_landmarks):
        dx_px = (current_landmark.x - previous_landmark.x) * frame_width
        dy_px = (current_landmark.y - previous_landmark.y) * frame_height
        movement_px = float(np.hypot(dx_px, dy_px))

        # Legacy implementation note cleaned for English demo.
        # Legacy implementation note cleaned for English demo.
        # Legacy implementation note cleaned for English demo.
        # Legacy implementation note cleaned for English demo.
        if movement_px <= dead_zone_px:
            x, y, z = previous_landmark.x, previous_landmark.y, previous_landmark.z
        else:
            x = previous_landmark.x + alpha * (current_landmark.x - previous_landmark.x)
            y = previous_landmark.y + alpha * (current_landmark.y - previous_landmark.y)
            z = previous_landmark.z + alpha * (current_landmark.z - previous_landmark.z)

        smoothed.append(
            SimpleLandmark(
                x=x,
                y=y,
                z=z,
                visibility=current_landmark.visibility,
                presence=current_landmark.presence,
            )
        )

    st.session_state[state_key] = smoothed
    return smoothed


def extract_face_blendshapes(face_results) -> dict[str, float]:
    """English documentation cleaned for investor demo."""
    if not getattr(face_results, "face_blendshapes", None):
        return {}
    if not face_results.face_blendshapes:
        return {}

    blendshapes = {}
    for category in face_results.face_blendshapes[0]:
        name = getattr(category, "category_name", "")
        score = safe_float(getattr(category, "score", 0.0), 0.0)
        if name:
            blendshapes[name] = score
    return blendshapes


def estimate_face_tracking_quality(
    face_landmarks: Optional[list[SimpleLandmark]],
    face_blendshapes: Optional[dict[str, float]],
) -> float:
    if not face_landmarks:
        return 0.0
    visible_points = [
        landmark
        for landmark in face_landmarks
        if min(landmark.visibility, landmark.presence) >= 0.35
    ]
    landmark_quality = len(visible_points) / max(len(face_landmarks), 1)
    blendshape_quality = 1.0 if face_blendshapes else 0.45
    return clamp((landmark_quality * 0.72 + blendshape_quality * 0.28) * 100)


def estimate_emotional_state(
    face_landmarks: Optional[list[SimpleLandmark]],
    face_blendshapes: Optional[dict[str, float]],
    jaw_clench_score: float,
) -> dict:
    """English documentation cleaned for investor demo."""
    blendshapes = face_blendshapes or {}

    def bs(name: str) -> float:
        return clamp(blendshapes.get(name, 0.0) * 100)

    brow_down = max(bs("browDownLeft"), bs("browDownRight"))
    brow_inner_up = bs("browInnerUp")
    brow_outer_up = max(bs("browOuterUpLeft"), bs("browOuterUpRight"))
    eye_squint = max(bs("eyeSquintLeft"), bs("eyeSquintRight"))
    eye_wide = max(bs("eyeWideLeft"), bs("eyeWideRight"))
    eye_blink = max(bs("eyeBlinkLeft"), bs("eyeBlinkRight"))
    cheek_squint = max(bs("cheekSquintLeft"), bs("cheekSquintRight"))
    mouth_press = max(bs("mouthPressLeft"), bs("mouthPressRight"))
    mouth_close = bs("mouthClose")
    mouth_frown = max(bs("mouthFrownLeft"), bs("mouthFrownRight"))
    mouth_smile = max(bs("mouthSmileLeft"), bs("mouthSmileRight"))
    mouth_pucker = bs("mouthPucker")
    jaw_open = bs("jawOpen")
    nose_sneer = max(bs("noseSneerLeft"), bs("noseSneerRight"))

    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    eye_landmark_focus = 0.0
    if face_landmarks and len(face_landmarks) > 386:
        left_eye_gap = abs(face_landmarks[159].y - face_landmarks[145].y)
        right_eye_gap = abs(face_landmarks[386].y - face_landmarks[374].y)
        avg_eye_gap = (left_eye_gap + right_eye_gap) / 2
        eye_landmark_focus = clamp((0.020 - avg_eye_gap) / 0.014 * 100)

    # Per-channel scores (0-100). Scaled so a single strong micro-expression
    # Legacy implementation note cleaned for English demo.
    # otherwise downstream arousal stays invisible. Real ARKit blendshapes
    # Legacy implementation note cleaned for English demo.
    brow_tension_score = clamp(brow_down * 0.95 + brow_inner_up * 0.45 + nose_sneer * 0.25)
    eye_focus_score = clamp(eye_squint * 0.50 + eye_landmark_focus * 0.30 + (100 - eye_blink) * 0.20)
    mouth_tension_score = clamp(
        mouth_press * 0.90 + mouth_close * 0.50 + mouth_frown * 0.55 + mouth_pucker * 0.30
    )
    eye_wide_score = clamp(eye_wide * 1.0)

    frustration = clamp(
        brow_tension_score * 0.40
        + mouth_tension_score * 0.32
        + jaw_clench_score * 0.22
        + eye_squint * 0.10
        + mouth_frown * 0.10
    )
    focus = clamp(
        eye_focus_score * 0.42
        + brow_down * 0.18
        + mouth_press * 0.16
        + (100 - max(mouth_smile, mouth_frown)) * 0.12
        + (100 - jaw_open) * 0.12
    )
    fatigue = clamp(
        eye_blink * 0.34
        + brow_inner_up * 0.22
        + mouth_frown * 0.18
        + (100 - eye_wide) * 0.14
        + jaw_clench_score * 0.12
    )
    surprise_stress = clamp(
        eye_wide * 0.34
        + jaw_open * 0.28
        + brow_outer_up * 0.22
        + brow_inner_up * 0.16
    )
    positive_release = clamp(mouth_smile * 0.62 + cheek_squint * 0.24 + (100 - mouth_frown) * 0.14)

    tension_peak = max(frustration, fatigue * 0.74, surprise_stress * 0.68)
    calm_control = clamp(100 - tension_peak * 0.72 - jaw_clench_score * 0.12 - brow_tension_score * 0.10)
    # MAX-based arousal: dominant single channel (brow / mouth / eye_wide /
    # jaw / sneer) drives the score, with a small additive bonus for
    # multi-channel co-firing. Cascaded sums diluted single signals to ~7
    # Legacy implementation note cleaned for English demo.
    facial_channels = (
        ("brow", brow_tension_score, 0.95),
        ("mouth", mouth_tension_score, 0.95),
        ("eye_wide", eye_wide_score, 0.88),
        ("jaw", jaw_clench_score, 0.78),
        ("sneer", nose_sneer, 0.78),
        ("eye_squint", eye_squint, 0.55),
    )
    dominant_weighted = 0.0
    dominant_label = "neutral"
    sum_facial = 0.0
    for label, value, weight in facial_channels:
        weighted = value * weight
        if weighted > dominant_weighted:
            dominant_weighted = weighted
            dominant_label = label
        sum_facial += value
    multi_channel_bonus = max(0.0, sum_facial - max(v for _, v, _ in facial_channels)) * 0.05
    emotional_arousal_score = clamp(
        dominant_weighted * 0.85
        + multi_channel_bonus
        + frustration * 0.10
        + surprise_stress * 0.12
        + fatigue * 0.06
    )
    emotional_valence_score = clamp(50 + positive_release * 0.38 - frustration * 0.30 - fatigue * 0.18)

    scores = {
        "focus": focus,
        "frustration": frustration,
        "fatigue": fatigue,
        "surprise_stress": surprise_stress,
        "release": positive_release,
        "calm_control": calm_control,
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    primary, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = clamp(42 + (top_score - second_score) * 0.9 + top_score * 0.18)

    if not face_landmarks or not blendshapes:
        primary = "no_face"
        confidence = 0.0

    return {
        "primary": primary,
        "confidence": confidence,
        "scores": scores,
        "brow_tension_score": brow_tension_score,
        "eye_focus_score": eye_focus_score,
        "mouth_tension_score": mouth_tension_score,
        "eye_wide_score": eye_wide_score,
        "arousal_score": emotional_arousal_score,
        "valence_score": emotional_valence_score,
        "dominant_facial_channel": dominant_label,
        "dominant_facial_value": round(dominant_weighted, 2),
    }


def calculate_biomechanics(
    pose_landmarks: list[SimpleLandmark],
    face_landmarks: Optional[list[SimpleLandmark]],
    face_blendshapes: Optional[dict[str, float]],
    frame_width: int,
    frame_height: int,
    shoulder_raise_ratio_threshold: float,
    shoulder_raise_sensitivity: float,
    asymmetry_threshold_percent: float,
    jaw_clench_ratio_threshold: float,
    baseline_profile: Optional[dict] = None,
) -> BiomechanicsMetrics:
    """English documentation cleaned for investor demo."""
    nose = pose_landmarks[vision.PoseLandmark.NOSE.value]
    left_shoulder = pose_landmarks[vision.PoseLandmark.LEFT_SHOULDER.value]
    right_shoulder = pose_landmarks[vision.PoseLandmark.RIGHT_SHOULDER.value]

    nose_x = nose.x * frame_width
    nose_y = nose.y * frame_height
    left_x = left_shoulder.x * frame_width
    left_y = left_shoulder.y * frame_height
    right_x = right_shoulder.x * frame_width
    right_y = right_shoulder.y * frame_height

    shoulder_width_px = max(abs(right_x - left_x), 1.0)
    mid_shoulder_y = (left_y + right_y) / 2
    nose_to_shoulders_px = max(mid_shoulder_y - nose_y, 1.0)
    nose_to_shoulders_ratio = nose_to_shoulders_px / shoulder_width_px

    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    if baseline_profile:
        baseline_ratio = baseline_profile.get(
            "nose_to_shoulders_ratio",
            shoulder_raise_ratio_threshold,
        )
        raised_delta = baseline_ratio - nose_to_shoulders_ratio
        neutral_margin = shoulder_raise_sensitivity * 0.18
        shoulder_elevation_score = clamp(
            (raised_delta - neutral_margin)
            / max(shoulder_raise_sensitivity * 0.72, 0.01)
            * 100
        )
    else:
        shoulder_elevation_score = clamp(
            (
                shoulder_raise_ratio_threshold - nose_to_shoulders_ratio
            )
            / max(shoulder_raise_sensitivity, 0.01)
            * 100
        )

    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    shoulder_delta_y = left_y - right_y
    shoulder_asymmetry_percent = abs(shoulder_delta_y) / shoulder_width_px * 100
    baseline_asymmetry = (
        baseline_profile.get("shoulder_asymmetry_percent", 0.0)
        if baseline_profile
        else 0.0
    )
    asymmetry_excess = max(
        shoulder_asymmetry_percent - baseline_asymmetry,
        0.0,
    )
    shoulder_asymmetry_score = clamp(
        (
            asymmetry_excess - asymmetry_threshold_percent
        )
        / max(asymmetry_threshold_percent * 1.8, 0.01)
        * 100
    )

    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    shoulder_slope_degrees = float(
        np.degrees(np.arctan2(shoulder_delta_y, max(abs(right_x - left_x), 1.0)))
    )

    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    jaw_open_ratio = None
    jaw_blendshape_score = 0.0
    jaw_clench_score = 0.0
    if face_landmarks and len(face_landmarks) > 300:
        upper_lip = face_landmarks[13]
        lower_lip = face_landmarks[14]
        face_top = face_landmarks[10]
        chin = face_landmarks[152]

        lip_gap_px = abs(lower_lip.y - upper_lip.y) * frame_height
        face_height_px = max(abs(chin.y - face_top.y) * frame_height, 1.0)
        jaw_open_ratio = lip_gap_px / face_height_px
        landmark_jaw_score = 0.0
        if baseline_profile and baseline_profile.get("jaw_open_ratio") is not None:
            baseline_jaw_open_ratio = baseline_profile["jaw_open_ratio"]
            landmark_jaw_score = clamp(
                (baseline_jaw_open_ratio - jaw_open_ratio)
                / max(baseline_jaw_open_ratio * 0.72, 0.001)
                * 100
            )
        else:
            # Legacy implementation note cleaned for English demo.
            # Legacy implementation note cleaned for English demo.
            # Legacy implementation note cleaned for English demo.
            # Legacy implementation note cleaned for English demo.
            landmark_jaw_score = clamp(
                (jaw_clench_ratio_threshold - jaw_open_ratio)
                / max(jaw_clench_ratio_threshold, 0.001)
                * 45
            )

        blendshapes = face_blendshapes or {}
        jaw_open_shape = blendshapes.get("jawOpen", 0.0)
        mouth_close = blendshapes.get("mouthClose", 0.0)
        mouth_press = max(
            blendshapes.get("mouthPressLeft", 0.0),
            blendshapes.get("mouthPressRight", 0.0),
        )
        mouth_tight = max(
            blendshapes.get("mouthShrugUpper", 0.0),
            blendshapes.get("mouthShrugLower", 0.0),
        )

        closed_jaw_score = clamp((0.16 - jaw_open_shape) / 0.16 * 100)
        press_score = clamp(max(mouth_close, mouth_press, mouth_tight) * 165)
        jaw_blendshape_score = clamp(closed_jaw_score * 0.25 + press_score * 0.75)
        jaw_clench_score = clamp(max(landmark_jaw_score, jaw_blendshape_score))

        relaxed = st.session_state.get("jaw_relaxed_baseline")
        clenched = st.session_state.get("jaw_clenched_baseline")
        if relaxed and clenched:
            relaxed_score = relaxed.get("jaw_blendshape_score", 0.0)
            clenched_score = clenched.get("jaw_blendshape_score", 100.0)
            denominator = max(clenched_score - relaxed_score, 8.0)
            personalized_blendshape_score = clamp(
                (jaw_blendshape_score - relaxed_score) / denominator * 100
            )
            relaxed_open = relaxed.get("jaw_open_ratio", jaw_open_ratio)
            clenched_open = clenched.get("jaw_open_ratio", jaw_open_ratio)
            open_denominator = max(relaxed_open - clenched_open, 0.004)
            personalized_open_score = clamp(
                (relaxed_open - jaw_open_ratio) / open_denominator * 100
            )
            jaw_clench_score = clamp(
                personalized_blendshape_score * 0.72
                + personalized_open_score * 0.28
            )

    emotion = estimate_emotional_state(
        face_landmarks=face_landmarks,
        face_blendshapes=face_blendshapes,
        jaw_clench_score=jaw_clench_score,
    )

    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    forward_head_baseline = (
        baseline_profile.get("nose_to_shoulders_ratio", 1.85)
        if baseline_profile else 1.85
    )
    forward_head_score = clamp(
        (forward_head_baseline - nose_to_shoulders_ratio)
        / max(forward_head_baseline * 0.40, 0.01)
        * 100
    )

    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    baseline_shoulder_width = (
        baseline_profile.get("shoulder_width_ratio")
        if baseline_profile else None
    )
    if baseline_shoulder_width and frame_width > 0:
        current_width_ratio = shoulder_width_px / max(frame_width, 1.0)
        shoulder_protraction_score = clamp(
            (baseline_shoulder_width - current_width_ratio)
            / max(baseline_shoulder_width * 0.30, 0.01)
            * 100
        )
    else:
        shoulder_protraction_score = 0.0

    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    posture_channels = (
        ("shoulders_up", shoulder_elevation_score, 0.95),
        ("forward_head", forward_head_score, 0.92),
        ("rolled_shoulders", shoulder_protraction_score, 0.90),
        ("asymmetric", shoulder_asymmetry_score, 0.70),
    )
    posture_dominant_weighted = 0.0
    posture_dominant_label = "neutral"
    posture_sum = 0.0
    for label, value, weight in posture_channels:
        weighted = value * weight
        if weighted > posture_dominant_weighted:
            posture_dominant_weighted = weighted
            posture_dominant_label = label
        posture_sum += value
    posture_supporting_bonus = max(0.0, posture_sum - max(v for _, v, _ in posture_channels)) * 0.06
    posture_stress_score = clamp(posture_dominant_weighted + posture_supporting_bonus)
    if posture_dominant_weighted < 14:
        posture_dominant_label = "neutral"

    raw_stress_score = clamp(
        posture_stress_score * 0.45
        + shoulder_elevation_score * 0.18
        + shoulder_asymmetry_score * 0.16
        + jaw_clench_score * 0.13
        + emotion["arousal_score"] * 0.08
    )

    triggers = []
    if shoulder_elevation_score >= 45:
        triggers.append("raised_shoulders")
    if shoulder_asymmetry_score >= 35:
        triggers.append("shoulder_asymmetry")
    if forward_head_score >= 45:
        triggers.append("forward_head")
    if shoulder_protraction_score >= 45:
        triggers.append("rolled_shoulders")
    if jaw_clench_score >= 55:
        triggers.append("jaw_clench")
    if emotion["arousal_score"] >= 62 and emotion["confidence"] >= 48:
        triggers.append("emotional_tension")

    return BiomechanicsMetrics(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        nose_x=nose_x,
        nose_y=nose_y,
        left_shoulder_x=left_x,
        left_shoulder_y=left_y,
        right_shoulder_x=right_x,
        right_shoulder_y=right_y,
        shoulder_width_px=shoulder_width_px,
        nose_to_shoulders_ratio=nose_to_shoulders_ratio,
        shoulder_elevation_score=shoulder_elevation_score,
        shoulder_asymmetry_percent=shoulder_asymmetry_percent,
        shoulder_asymmetry_score=shoulder_asymmetry_score,
        shoulder_slope_degrees=shoulder_slope_degrees,
        jaw_open_ratio=jaw_open_ratio,
        jaw_blendshape_score=jaw_blendshape_score,
        jaw_clench_score=jaw_clench_score,
        brow_tension_score=emotion["brow_tension_score"],
        eye_focus_score=emotion["eye_focus_score"],
        mouth_tension_score=emotion["mouth_tension_score"],
        emotional_arousal_score=emotion["arousal_score"],
        emotional_valence_score=emotion["valence_score"],
        emotion_primary=emotion["primary"],
        emotion_confidence=emotion["confidence"],
        emotion_scores=emotion["scores"],
        raw_stress_score=raw_stress_score,
        tilt_score=st.session_state.tilt_score,
        triggers=triggers,
        forward_head_score=forward_head_score,
        shoulder_protraction_score=shoulder_protraction_score,
        posture_stress_score=posture_stress_score,
        dominant_posture_channel=posture_dominant_label,
        dominant_posture_value=round(posture_dominant_weighted, 2),
    )


def update_tilt_meter(metrics: BiomechanicsMetrics) -> float:
    """English documentation cleaned for investor demo."""
    now = time.time()
    last_update = st.session_state.last_tilt_update_at or now
    dt = min(max(now - last_update, 0.0), 0.5)
    st.session_state.last_tilt_update_at = now

    # Use posture_stress_score (forward head + rolled shoulders +
    # Legacy implementation note cleaned for English demo.
    # stronger than elevation alone. Shoulder-asymmetry stays as a
    # separate channel so a one-sided lean still counts independently.
    posture_channel = max(
        metrics.shoulder_elevation_score,
        metrics.posture_stress_score * 0.92,
    )
    result = scoring.update_tilt_accumulator(
        shoulder_elevation=posture_channel,
        shoulder_asymmetry=metrics.shoulder_asymmetry_score,
        jaw_clench=metrics.jaw_clench_score,
        emotional_arousal=metrics.emotional_arousal_score,
        emotion_confidence=metrics.emotion_confidence,
        raw_stress=metrics.raw_stress_score,
        previous=scoring.TiltAccumulatorState(
            shoulder_seconds=st.session_state.shoulder_elevated_seconds,
            asymmetry_seconds=st.session_state.asymmetry_seconds,
            jaw_seconds=st.session_state.jaw_clench_seconds,
            emotion_seconds=st.session_state.emotional_tension_seconds,
            tilt_score=st.session_state.tilt_score,
        ),
        dt=dt,
    )
    st.session_state.shoulder_elevated_seconds = result.shoulder_seconds
    st.session_state.asymmetry_seconds = result.asymmetry_seconds
    st.session_state.jaw_clench_seconds = result.jaw_seconds
    st.session_state.emotional_tension_seconds = result.emotion_seconds
    st.session_state.tilt_score = result.tilt_score
    metrics.tilt_score = st.session_state.tilt_score
    return st.session_state.tilt_score


def start_calibration() -> None:
    st.session_state.is_calibrating = True
    st.session_state.calibration_started_at = time.time()
    st.session_state.calibration_samples = []
    st.session_state.baseline_profile = None
    reset_runtime_state(keep_alert=False)
    st.session_state.pipeline_status = "Calibrating neutral posture"


def update_calibration(metrics: BiomechanicsMetrics) -> None:
    if not st.session_state.is_calibrating:
        return

    st.session_state.calibration_samples.append(
        {
            "nose_to_shoulders_ratio": metrics.nose_to_shoulders_ratio,
            "shoulder_asymmetry_percent": metrics.shoulder_asymmetry_percent,
            "jaw_open_ratio": metrics.jaw_open_ratio,
            "jaw_blendshape_score": metrics.jaw_blendshape_score,
        }
    )

    elapsed = time.time() - st.session_state.calibration_started_at
    if elapsed < st.session_state.calibration_duration:
        st.session_state.pipeline_status = (
            f"Baseline calibration: {elapsed:.1f}s / "
            f"{st.session_state.calibration_duration:.0f}s"
        )
        return

    samples = st.session_state.calibration_samples
    jaw_samples = [
        sample["jaw_open_ratio"]
        for sample in samples
        if sample["jaw_open_ratio"] is not None
    ]
    st.session_state.baseline_profile = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_count": len(samples),
        "nose_to_shoulders_ratio": float(
            np.median([sample["nose_to_shoulders_ratio"] for sample in samples])
        ),
        "shoulder_asymmetry_percent": float(
            np.median([sample["shoulder_asymmetry_percent"] for sample in samples])
        ),
        "jaw_open_ratio": float(np.median(jaw_samples)) if jaw_samples else None,
        "jaw_blendshape_score": float(
            np.median([sample["jaw_blendshape_score"] for sample in samples])
        ),
    }
    st.session_state.is_calibrating = False
    st.session_state.calibration_samples = []
    reset_runtime_state(keep_alert=False)
    st.session_state.pipeline_status = "Baseline ready: personal model active"


def start_jaw_calibration_phase(phase: str) -> None:
    st.session_state.jaw_calibration_phase = phase
    st.session_state.jaw_calibration_samples = []
    st.session_state.pipeline_status = (
        "Jaw calibration: relax"
        if phase == "relaxed"
        else "Jaw calibration: clench"
    )


def update_jaw_calibration(metrics: BiomechanicsMetrics) -> None:
    phase = st.session_state.jaw_calibration_phase
    if not phase:
        return
    if metrics.jaw_open_ratio is None:
        return

    st.session_state.jaw_calibration_samples.append(
        {
            "jaw_open_ratio": metrics.jaw_open_ratio,
            "jaw_blendshape_score": metrics.jaw_blendshape_score,
            "jaw_clench_score": metrics.jaw_clench_score,
        }
    )
    if len(st.session_state.jaw_calibration_samples) < 18:
        return

    samples = st.session_state.jaw_calibration_samples
    profile = {
        "jaw_open_ratio": float(np.median([sample["jaw_open_ratio"] for sample in samples])),
        "jaw_blendshape_score": float(
            np.median([sample["jaw_blendshape_score"] for sample in samples])
        ),
        "jaw_clench_score": float(
            np.median([sample["jaw_clench_score"] for sample in samples])
        ),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if phase == "relaxed":
        st.session_state.jaw_relaxed_baseline = profile
        st.session_state.pipeline_status = "Jaw: relaxed baseline ready"
    else:
        st.session_state.jaw_clenched_baseline = profile
        st.session_state.pipeline_status = "Jaw: clenched baseline ready"
    st.session_state.jaw_calibration_phase = ""
    st.session_state.jaw_calibration_samples = []


def calibration_progress() -> float:
    if not st.session_state.is_calibrating:
        return 1.0 if st.session_state.baseline_profile else 0.0
    elapsed = time.time() - st.session_state.calibration_started_at
    return clamp(elapsed / max(st.session_state.calibration_duration, 0.1), 0.0, 1.0)


def record_game_event(event_name: str) -> None:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event_name,
        "tilt_score": round(float(st.session_state.tilt_score), 2),
        "session_id": st.session_state.session_id,
    }
    st.session_state.game_events.appendleft(event)
    st.session_state.last_game_event = event_name


def append_session_series(metrics: BiomechanicsMetrics) -> None:
    st.session_state.session_series.append(
        {
            "t": datetime.now().strftime("%H:%M:%S"),
            "epoch": time.time(),
            "tilt": round(float(metrics.tilt_score), 2),
            "raw": round(float(metrics.raw_stress_score), 2),
            "emotion": metrics.emotion_primary,
            "emotion_confidence": round(float(metrics.emotion_confidence), 2),
            "arousal": round(float(metrics.emotional_arousal_score), 2),
            "valence": round(float(metrics.emotional_valence_score), 2),
            "jaw": round(float(metrics.jaw_clench_score), 2),
            "shoulders": round(float(metrics.shoulder_elevation_score), 2),
            "asymmetry": round(float(metrics.shoulder_asymmetry_score), 2),
            "voice": round(float((st.session_state.get("voice_metrics") or {}).get("voice_tension_score", 0.0)), 2),
            "input": round(float((st.session_state.get("input_metrics") or {}).get("input_chaos_score", 0.0)), 2),
            "recovery": round(float(st.session_state.recovery_score), 2),
            "alert": st.session_state.coach_alert,
            "event": st.session_state.last_game_event,
            "triggers": list(metrics.triggers),
        }
    )
    st.session_state.last_game_event = ""


def count_file_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as file:
            return sum(1 for _ in file)
    except OSError:
        return 0


def active_modalities() -> list[str]:
    modalities = ["pose", "face", "emotion"]
    if st.session_state.get("enable_voice_layer") or st.session_state.get("voice_metrics"):
        modalities.append("voice")
    if st.session_state.get("enable_input_telemetry") or st.session_state.get("input_metrics"):
        modalities.append("mouse_keyboard")
    if st.session_state.game_events:
        modalities.append("game_events")
    if st.session_state.recovery_score > 0 or st.session_state.recovery_seconds is not None:
        modalities.append("recovery")
    return modalities


def build_data_moat_stats() -> dict:
    profile_count = 0
    if PROFILES_DIR.exists():
        profile_count = len(list(PROFILES_DIR.glob("*.json")))
    return {
        "auto_label_samples": count_file_lines(AFFECTIVE_DATASET_PATH),
        "somatic_log_rows": count_file_lines(SOMATIC_LOG_PATH),
        "api_events": count_file_lines(BODY_LANGUAGE_API_PATH),
        "profiles": profile_count,
        "session_samples": len(st.session_state.session_series),
        "modalities": active_modalities(),
        "baseline_active": bool(st.session_state.baseline_profile),
        "jaw_baseline_active": bool(
            st.session_state.jaw_relaxed_baseline
            and st.session_state.jaw_clenched_baseline
        ),
    }


def coach_command_effectiveness() -> dict:
    series = list(st.session_state.session_series)
    alert_indices = [
        index
        for index, row in enumerate(series)
        if row.get("alert")
    ]
    if not alert_indices:
        return {
            "status": "no alert yet",
            "best_command": "n/a",
            "effectiveness": 0.0,
            "tilt_drop": 0.0,
            "recovery_seconds": st.session_state.recovery_seconds,
        }

    best = None
    for index in alert_indices:
        before = series[index]
        after_window = series[index : min(index + 24, len(series))]
        if not after_window:
            continue
        min_after_tilt = min(float(row.get("tilt", 0.0)) for row in after_window)
        start_tilt = float(before.get("tilt", 0.0))
        tilt_drop = max(start_tilt - min_after_tilt, 0.0)
        arousal_drop = max(
            float(before.get("arousal", 0.0))
            - min(float(row.get("arousal", 0.0)) for row in after_window),
            0.0,
        )
        jaw_drop = max(
            float(before.get("jaw", 0.0))
            - min(float(row.get("jaw", 0.0)) for row in after_window),
            0.0,
        )
        score = clamp(tilt_drop * 0.72 + arousal_drop * 0.18 + jaw_drop * 0.10)
        candidate = {
            "status": "measured",
            "best_command": before.get("alert", "n/a"),
            "effectiveness": round(score, 1),
            "tilt_drop": round(tilt_drop, 1),
            "arousal_drop": round(arousal_drop, 1),
            "jaw_drop": round(jaw_drop, 1),
            "recovery_seconds": st.session_state.recovery_seconds,
        }
        if best is None or candidate["effectiveness"] > best["effectiveness"]:
            best = candidate
    return best or {
        "status": ',',
        "best_command": series[alert_indices[-1]].get("alert", "n/a"),
        "effectiveness": 0.0,
        "tilt_drop": 0.0,
        "recovery_seconds": st.session_state.recovery_seconds,
    }


def build_causal_intervention_graph() -> dict:
    """English documentation cleaned for investor demo."""
    series = list(st.session_state.session_series)
    if len(series) < 12:
        return {
            "status": "collecting signal",
            "causal_confidence": 0.0,
            "tilt_prevented": 0.0,
            "counterfactual_peak": 0.0,
            "actual_after": 0.0,
            "intervention_index": None,
            "trigger_event": "n/a",
            "body_driver": "n/a",
            "emotion_driver": "n/a",
            "coach_command": "n/a",
            "nodes": [],
            "edges": [],
            "chart": None,
        }

    tilts = np.array([float(row.get("tilt", 0.0)) for row in series], dtype=float)
    alert_indices = [index for index, row in enumerate(series) if row.get("alert")]
    event_indices = [index for index, row in enumerate(series) if row.get("event")]
    peak_index = int(np.argmax(tilts))
    intervention_index = alert_indices[0] if alert_indices else max(peak_index - 4, 0)
    trigger_event_index = (
        max([index for index in event_indices if index <= intervention_index], default=None)
    )
    trigger_event = (
        series[trigger_event_index].get("event", "n/a")
        if trigger_event_index is not None
        else "no_event"
    )

    pre_start = max(intervention_index - 12, 0)
    pre_window = tilts[pre_start : intervention_index + 1]
    if len(pre_window) >= 3:
        x = np.arange(len(pre_window), dtype=float)
        pre_slope = float(np.polyfit(x, pre_window, 1)[0])
    else:
        pre_slope = 0.0
    pre_slope = max(pre_slope, 0.15)

    horizon = min(28, len(series) - intervention_index - 1)
    if horizon <= 1:
        horizon = min(16, len(series) - 1)
    start_tilt = float(tilts[intervention_index])
    counterfactual = []
    actual = []
    labels = []
    for step in range(horizon + 1):
        idx = min(intervention_index + step, len(series) - 1)
        actual_value = float(tilts[idx])
        predicted = clamp(start_tilt + pre_slope * step * 1.15)
        actual.append(actual_value)
        counterfactual.append(predicted)
        labels.append(series[idx].get("t", str(step)))

    counterfactual_peak = float(max(counterfactual)) if counterfactual else start_tilt
    actual_after = float(actual[-1]) if actual else start_tilt
    tilt_prevented = max(counterfactual_peak - actual_after, 0.0)
    actual_drop = max(start_tilt - actual_after, 0.0)

    pressure_scores = {
        "jaw": float(series[peak_index].get("jaw", 0.0)),
        "shoulders": float(series[peak_index].get("shoulders", 0.0)),
        "arousal": float(series[peak_index].get("arousal", 0.0)),
        "input": float(series[peak_index].get("input", 0.0)),
        "voice": float(series[peak_index].get("voice", 0.0)),
    }
    body_driver = max(pressure_scores, key=pressure_scores.get)
    emotion_driver = series[peak_index].get("emotion", "auto-label")
    coach_command = series[intervention_index].get("alert") or st.session_state.coach_alert or "n/a"
    has_event = trigger_event_index is not None
    has_command = bool(coach_command and coach_command != "n/a")
    confidence = clamp(
        32
        + min(len(series), 90) * 0.28
        + tilt_prevented * 0.55
        + actual_drop * 0.35
        + (15 if has_event else 0)
        + (14 if has_command else 0)
    )
    nodes = [
        {"id": "event", "label": trigger_event, "type": "game_event"},
        {"id": "body", "label": body_driver, "type": "somatic_driver"},
        {"id": "emotion", "label": emotion_driver, "type": "affective_state"},
        {"id": "coach", "label": coach_command, "type": "intervention"},
        {"id": "recovery", "label": f"{actual_drop:.0f} tilt drop", "type": "outcome"},
    ]
    edges = [
        {"from": "event", "to": "body", "weight": round(pressure_scores[body_driver], 1)},
        {"from": "body", "to": "emotion", "weight": round(float(series[peak_index].get("arousal", 0.0)), 1)},
        {"from": "emotion", "to": "coach", "weight": round(start_tilt, 1)},
        {"from": "coach", "to": "recovery", "weight": round(actual_drop, 1)},
    ]
    return {
        "status": "causal graph ready",
        "causal_confidence": round(confidence, 1),
        "tilt_prevented": round(tilt_prevented, 1),
        "counterfactual_peak": round(counterfactual_peak, 1),
        "actual_after": round(actual_after, 1),
        "intervention_index": intervention_index,
        "trigger_event": trigger_event,
        "body_driver": body_driver,
        "emotion_driver": emotion_driver,
        "coach_command": coach_command,
        "nodes": nodes,
        "edges": edges,
        "chart": {
            "Actual tilt": actual,
            "No coach counterfactual": counterfactual,
        },
    }


def generate_recovery_protocol(report: Optional[dict] = None) -> dict:
    report = report or st.session_state.post_match_report or build_post_match_report()
    pressure = report.get("what_broke_state", "n/a")
    protocols = {
        "jaw": {
            "name": "Jaw Unlock 20",
            "cue": "Unclench teeth. Tongue down. Exhale.",
            "steps": [
                "Separate teeth for 2 seconds",
                "Drop tongue to the floor of the mouth",
                "Exhale slowly through the nose",
            ],
        },
        "shoulders": {
            "name": "Shoulder Drop Reset",
            "cue": "Drop shoulders. Keep chest wide.",
            "steps": [
                "Let shoulders fall heavy",
                "Widen chest without leaning in",
                "Hold soft posture for the next fight",
            ],
        },
        "input": {
            "name": "Hands Slowdown",
            "cue": "Slow hands. One clean action.",
            "steps": [
                "Relax grip for 2 seconds",
                "Reset mouse hand",
                "Return to one deliberate input at a time",
            ],
        },
        "voice": {
            "name": "Voice Downshift",
            "cue": "Lower voice. Short comms only.",
            "steps": [
                "Lower speaking volume",
                "Use short calls only",
                "Pause before the next comm",
            ],
        },
        "face": {
            "name": "Face Neutral Reset",
            "cue": "Soften face. Widen attention.",
            "steps": [
                "Release brow and mouth pressure",
                "Use peripheral vision",
                'next fight',
            ],
        },
    }
    return protocols.get(
        pressure,
        {
            "name": "Baseline Stabilizer",
            "cue": "Exhale. Reset jaw and shoulders.",
            "steps": [
                "Slow exhale",
                "Drop shoulders",
                "Return to neutral posture",
            ],
        },
    )


def append_body_language_api_event(metrics: BiomechanicsMetrics) -> None:
    now = time.time()
    if now - st.session_state.last_api_event_write_at < 1.0:
        return
    st.session_state.last_api_event_write_at = now
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "type": "human_state.update",
        "schema_version": "kinaesthetic.body_language_api.v0",
        "timestamp": metrics.timestamp,
        "session_id": st.session_state.session_id,
        "state": {
            "tilt": round(metrics.tilt_score, 1),
            "emotion": metrics.emotion_primary,
            "emotion_confidence": round(metrics.emotion_confidence, 1),
            "arousal": round(metrics.emotional_arousal_score, 1),
            "valence": round(metrics.emotional_valence_score, 1),
            "recovery_score": round(float(st.session_state.recovery_score), 1),
        },
        "signals": {
            "shoulders": round(metrics.shoulder_elevation_score, 1),
            "jaw": round(metrics.jaw_clench_score, 1),
            "voice": round(float((st.session_state.get("voice_metrics") or {}).get("voice_tension_score", 0.0)), 1),
            "input_chaos": round(float((st.session_state.get("input_metrics") or {}).get("input_chaos_score", 0.0)), 1),
        },
        "triggers": metrics.triggers,
        "coach_alert": st.session_state.coach_alert,
    }
    with BODY_LANGUAGE_API_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def export_pitch_report() -> Optional[Path]:
    report = build_post_match_report()
    moat = build_data_moat_stats()
    command = coach_command_effectiveness()
    protocol = generate_recovery_protocol(report)
    causal = build_causal_intervention_graph()
    signature = build_somatic_signature()
    next_best = recommend_next_best_intervention()
    language = build_somatic_language_engine()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"pitch_report_{st.session_state.session_id}.md"
    content = f"""# Kinaesthetic AI Session Intelligence

## Founder Narrative
Games optimize engagement. Kinaesthetic AI optimizes the human.

## Breakdown
- What broke state: {report.get('what_broke_state', 'n/a')}
- Breakdown moment: {report.get('breakdown_moment', 'n/a')}
- Dominant emotion: {report.get('dominant_emotion', 'n/a')}
- Trigger event: {report.get('trigger_event', 'n/a')}

## Recovery
- Recovery score: {report.get('recovery_score', 0)}%
- Winning command: {command.get('best_command', 'n/a')}
- Command effectiveness: {command.get('effectiveness', 0)}%
- Protocol: {protocol['name']}     {protocol['cue']}
- Next best intervention: {next_best.get('cue', 'n/a')}

## Causal Intervention
- Causal confidence: {causal.get('causal_confidence', 0)}%
- Tilt prevented: {causal.get('tilt_prevented', 0)}
- Counterfactual peak without coach: {causal.get('counterfactual_peak', 0)}%
- Actual after intervention: {causal.get('actual_after', 0)}%
- Causal path: {causal.get('trigger_event', 'n/a')} -> {causal.get('body_driver', 'n/a')} -> {causal.get('emotion_driver', 'n/a')} -> coach -> recovery

## Data Moat
- Auto-label samples: {moat['auto_label_samples']}
- Session samples: {moat['session_samples']}
- Modalities: {', '.join(moat['modalities'])}
- Profiles: {moat['profiles']}
- API events: {moat['api_events']}

## Somatic Twin Signature
- Signature hash: {signature.get('signature_hash', 'pending')}
- Dominant lock: {signature.get('dominant_lock', 'n/a')}
- Stress entry speed: {signature.get('stress_entry_speed', 0)}
- Recovery half-life: {signature.get('recovery_half_life', 'n/a')}
- Resilience score: {signature.get('resilience_score', 0)}%

## Somatic Language
- Alphabet size: {language.get('alphabet_size', 0)}
- Entropy: {language.get('entropy', 0)}%
- Current phrase: {language.get('current_phrase', 'n/a')}
- Next token forecast: {language.get('next_token', 'n/a')} ({language.get('next_confidence', 0)}%)
- Sentence: {language.get('sentence', 'WAIT_FOR_SIGNAL')}

## Recommendation
{report.get('coach_recommendation', 'n/a')}
"""
    path.write_text(content, encoding="utf-8")
    st.session_state.last_exported_report_path = str(path)
    return path


def generate_investor_demo_session() -> None:
    """English documentation cleaned for investor demo."""
    reset_runtime_state()
    st.session_state.running = False
    close_camera()
    st.session_state.session_id = "investor-demo-" + datetime.now(timezone.utc).strftime("%H%M%S")
    st.session_state.session_series.clear()
    st.session_state.game_events.clear()
    st.session_state.somatic_logs.clear()
    st.session_state.coach_alert = "UNLOCK JAW. DROP SHOULDERS."
    st.session_state.recovery_score = 84.0
    st.session_state.recovery_seconds = 11.8
    st.session_state.recovery_active = False

    now = time.time()
    base_clock = datetime.now()
    scenario_events = {
        24: "death",
        32: "toxic_chat",
        48: "viewer_reset",
        62: "clutch",
    }
    rows = []
    for index in range(90):
        phase = index / 89
        if index < 22:
            tilt = 18 + math.sin(index / 4) * 3
            arousal = 22
            jaw = 14
            shoulders = 18
            input_chaos = 12
            voice = 10
            emotion = "calm_control"
            alert = ""
            triggers = []
        elif index < 42:
            spike = (index - 22) / 20
            tilt = 25 + spike * 68
            arousal = 35 + spike * 56
            jaw = 28 + spike * 62
            shoulders = 30 + spike * 52
            input_chaos = 24 + spike * 66
            voice = 18 + spike * 48
            emotion = "frustration_rising"
            alert = ""
            triggers = ["jaw_clench", "emotional_tension", "raised_shoulders"]
        elif index < 56:
            tilt = 92 - (index - 42) * 1.2
            arousal = 86 - (index - 42) * 1.7
            jaw = 88 - (index - 42) * 2.2
            shoulders = 80 - (index - 42) * 1.6
            input_chaos = 84 - (index - 42) * 3.0
            voice = 62 - (index - 42) * 2.0
            emotion = "high_pressure"
            alert = "UNLOCK JAW. DROP SHOULDERS."
            triggers = ["jaw_clench", "emotional_tension"]
        else:
            recovery_phase = (index - 56) / 34
            tilt = 74 - recovery_phase * 48
            arousal = 62 - recovery_phase * 38
            jaw = 55 - recovery_phase * 36
            shoulders = 52 - recovery_phase * 30
            input_chaos = 36 - recovery_phase * 20
            voice = 28 - recovery_phase * 12
            emotion = "recovery"
            alert = "BREATHE OUT. KEEP SHOULDERS LOW." if index < 66 else ""
            triggers = ["recovery"] if index < 72 else []

        event = scenario_events.get(index, "")
        row = {
            "t": (base_clock).strftime("%H:%M:%S"),
            "epoch": now + index,
            "tilt": round(clamp(tilt), 2),
            "raw": round(clamp(arousal * 0.48 + jaw * 0.24 + shoulders * 0.18 + input_chaos * 0.10), 2),
            "emotion": emotion,
            "emotion_confidence": 82.0 if emotion != "calm_control" else 76.0,
            "arousal": round(clamp(arousal), 2),
            "valence": round(clamp(62 - arousal * 0.32 + phase * 18), 2),
            "jaw": round(clamp(jaw), 2),
            "shoulders": round(clamp(shoulders), 2),
            "asymmetry": round(clamp(10 + shoulders * 0.22), 2),
            "voice": round(clamp(voice), 2),
            "input": round(clamp(input_chaos), 2),
            "recovery": round(clamp(phase * 100), 2),
            "alert": alert,
            "event": event,
            "triggers": triggers,
        }
        rows.append(row)
        if event:
            st.session_state.game_events.appendleft(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "event": event,
                    "tilt_score": row["tilt"],
                    "session_id": st.session_state.session_id,
                }
            )

    st.session_state.session_series.extend(rows)
    st.session_state.tilt_score = rows[-1]["tilt"]
    st.session_state.voice_metrics = {
        "voice_tension_score": rows[-1]["voice"],
        "speech_rate_proxy": 18.0,
        "voice_status": "Investor demo synthetic voice layer",
    }
    st.session_state.input_metrics = {
        "input_chaos_score": rows[-1]["input"],
        "input_status": "Investor demo synthetic input telemetry",
    }
    st.session_state.tilt_prediction = {
        "seconds": None,
        "confidence": 91.0,
        "slope_per_sec": -1.42,
        "label": "recovery confirmed",
    }
    build_somatic_fingerprint()
    build_post_match_report()
    st.session_state.pipeline_status = 'Investor Demo Mode: recovery'


def update_tilt_prediction() -> dict:
    """English documentation cleaned for investor demo."""
    series = list(st.session_state.session_series)[-24:]
    prediction = {
        "seconds": None,
        "confidence": 0.0,
        "slope_per_sec": 0.0,
        "label": "collecting signal",
    }
    if len(series) < 5:
        st.session_state.tilt_prediction = prediction
        return prediction

    times = np.array([row["epoch"] for row in series], dtype=float)
    tilts = np.array([row["tilt"] for row in series], dtype=float)
    times = times - times[0]
    if float(times[-1]) <= 0:
        st.session_state.tilt_prediction = prediction
        return prediction

    slope, intercept = np.polyfit(times, tilts, 1)
    slope = float(slope)
    current_tilt = float(tilts[-1])

    if slope <= 0.25:
        prediction.update(
            {
                "confidence": clamp(abs(slope) * 18, 0, 30),
                "slope_per_sec": slope,
                "label": "stable",
            }
        )
    else:
        seconds_to_threshold = (
            st.session_state.alert_threshold - current_tilt
        ) / max(slope, 0.001)
        seconds_to_threshold = max(float(seconds_to_threshold), 0.0)
        confidence = clamp(38 + slope * 18 + current_tilt * 0.35, 0, 96)
        prediction.update(
            {
                "seconds": seconds_to_threshold,
                "confidence": confidence,
                "slope_per_sec": slope,
                "label": "rising",
            }
        )

    st.session_state.tilt_prediction = prediction
    return prediction


def build_somatic_fingerprint() -> dict:
    series = list(st.session_state.session_series)
    metrics = st.session_state.current_metrics
    if not series and not metrics:
        return {
            "archetype": "Waiting for signal",
            "dominant_pattern": "waiting_for_pose",
            "recovery_speed": "unknown",
            "readiness": 0,
        }

    trigger_counts = {
        "raised_shoulders": 0,
        "shoulder_asymmetry": 0,
        "jaw_clench": 0,
        "emotional_tension": 0,
    }
    for row in series:
        for trigger in row.get("triggers", []):
            if trigger in trigger_counts:
                trigger_counts[trigger] += 1

    dominant_pattern = max(trigger_counts, key=trigger_counts.get)
    if trigger_counts[dominant_pattern] == 0 and metrics:
        if metrics.shoulder_elevation_score >= metrics.shoulder_asymmetry_score:
            dominant_pattern = "raised_shoulders"
        else:
            dominant_pattern = "shoulder_asymmetry"

    archetypes = {
        "raised_shoulders": "Shoulder Loader",
        "shoulder_asymmetry": "Asymmetric Lean",
        "jaw_clench": "Jaw Locker",
        "emotional_tension": "Face Tension Spike",
    }
    tilts = [float(row["tilt"]) for row in series[-90:]]
    avg_tilt = float(np.mean(tilts)) if tilts else float(st.session_state.tilt_score)
    peak_tilt = float(np.max(tilts)) if tilts else float(st.session_state.tilt_score)
    recovery_speed = "stable"
    if len(tilts) >= 8 and tilts[-1] > tilts[0]:
        recovery_speed = "tilt rising"
    elif peak_tilt > 70 and tilts and tilts[-1] > 45:
        recovery_speed = "needs reset"

    readiness = clamp(100 - avg_tilt * 0.72 - max(peak_tilt - 75, 0) * 0.55)
    fingerprint = {
        "archetype": archetypes.get(dominant_pattern, "Balanced Operator"),
        "dominant_pattern": dominant_pattern,
        "recovery_speed": recovery_speed,
        "avg_tilt": round(avg_tilt, 1),
        "peak_tilt": round(peak_tilt, 1),
        "readiness": round(readiness, 1),
        "baseline_active": bool(st.session_state.baseline_profile),
    }
    st.session_state.somatic_fingerprint = fingerprint
    return fingerprint


def build_post_match_report() -> dict:
    series = list(st.session_state.session_series)
    events = list(st.session_state.game_events)
    if not series:
        report = {
            "headline": "No session yet",
            "peak_tilt": 0,
            "avg_tilt": 0,
            "event_count": len(events),
            "insight": ',',
            "recovery_note": "n/a",
            "breakdown_moment": "n/a",
            "dominant_emotion": "n/a",
            "trigger_event": "n/a",
            "what_broke_state": "n/a",
            "winning_command": "n/a",
            "recovery_score": 0,
            "coach_recommendation": '60-90',
        }
        st.session_state.post_match_report = report
        return report

    tilts = np.array([row["tilt"] for row in series], dtype=float)
    arousals = np.array([row.get("arousal", 0.0) for row in series], dtype=float)
    jaws = np.array([row.get("jaw", 0.0) for row in series], dtype=float)
    shoulders = np.array([row.get("shoulders", 0.0) for row in series], dtype=float)
    inputs = np.array([row.get("input", 0.0) for row in series], dtype=float)
    voices = np.array([row.get("voice", 0.0) for row in series], dtype=float)
    peak_tilt = float(np.max(tilts))
    avg_tilt = float(np.mean(tilts))
    peak_index = int(np.argmax(tilts))
    peak_row = series[peak_index]
    high_tilt_frames = int(np.sum(tilts >= st.session_state.alert_threshold))
    fingerprint = build_somatic_fingerprint()
    recovery_score = float(st.session_state.recovery_score)
    if st.session_state.recovery_seconds is not None:
        recovery_note = f"Recovery             {st.session_state.recovery_seconds}s."
    elif st.session_state.recovery_active:
        recovery_note = 'Recovery :'
    elif len(tilts) >= 10 and tilts[-1] > avg_tilt:
        recovery_note = ';'
    else:
        recovery_note = "Recovery proof appears after a live alert and reset."

    if high_tilt_frames:
        insight = (
            f"High tilt persisted for {high_tilt_frames} samples; "
            f"dominant body pattern: {fingerprint['archetype']}."
        )
    elif events:
        insight = (
            f"Game events marked: {len(events)}; tracking body-state response."
        )
    else:
        insight = ':'

    if peak_tilt >= 85:
        recovery_note = 'reset'

    emotion_counts = {}
    for row in series:
        emotion = row.get("emotion") or "n/a"
        emotion_counts[emotion] = emotion_counts.get(emotion, 0) + 1
    dominant_emotion = max(emotion_counts, key=emotion_counts.get) if emotion_counts else "n/a"

    event_rows = [row for row in series if row.get("event")]
    trigger_event = "no_event"
    if event_rows:
        trigger_event = max(event_rows, key=lambda row: row.get("tilt", 0)).get("event", "n/a")

    pressure_scores = {
        "jaw": float(np.mean(jaws[-30:])) if len(jaws) else 0.0,
        "shoulders": float(np.mean(shoulders[-30:])) if len(shoulders) else 0.0,
        "arousal": float(np.mean(arousals[-30:])) if len(arousals) else 0.0,
        "input": float(np.mean(inputs[-30:])) if len(inputs) else 0.0,
        "voice": float(np.mean(voices[-30:])) if len(voices) else 0.0,
    }
    what_broke_state = max(pressure_scores, key=pressure_scores.get)
    winning_command = "n/a"
    alert_rows = [row for row in series if row.get("alert")]
    if alert_rows:
        winning_command = alert_rows[-1].get("alert", "n/a")

    coach_recommendation = 'baseline calibration'
    if what_broke_state == "jaw":
        coach_recommendation = "Jaw unlock: tongue down, exhale, loosen teeth."
    elif what_broke_state == "shoulders":
        coach_recommendation = "Shoulder reset: drop shoulders, widen chest, slow breath."
    elif what_broke_state == '/':
        coach_recommendation = 'mouse/keyboard'
    elif what_broke_state == "arousal":
        coach_recommendation = "Reset intensity before the next fight."
    elif what_broke_state == "voice":
        coach_recommendation = "Lower comms volume and shorten callouts."

    report = {
        "headline": "Session proof ready",
        "peak_tilt": round(peak_tilt, 1),
        "avg_tilt": round(avg_tilt, 1),
        "event_count": len(events),
        "insight": insight,
        "recovery_note": recovery_note,
        "fingerprint": fingerprint,
        "breakdown_moment": f"{peak_row.get('t', 'n/a')}    tilt {peak_tilt:.0f}%",
        "dominant_emotion": dominant_emotion,
        "trigger_event": trigger_event,
        "what_broke_state": what_broke_state,
        "pressure_scores": {key: round(value, 1) for key, value in pressure_scores.items()},
        "winning_command": winning_command,
        "recovery_score": round(recovery_score, 1),
        "coach_recommendation": coach_recommendation,
    }
    st.session_state.post_match_report = report
    return report


def ingest_external_game_events() -> None:
    """English documentation cleaned for investor demo."""
    if not EXTERNAL_EVENTS_INBOX_PATH.exists():
        return

    lines = EXTERNAL_EVENTS_INBOX_PATH.read_text(encoding="utf-8").splitlines()
    offset = min(st.session_state.external_event_offset, len(lines))
    for line in lines[offset:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_name = str(payload.get("event", "external_event"))
        source = str(payload.get("source", "external"))
        record_game_event(f"{source}:{event_name}")
    st.session_state.external_event_offset = len(lines)


def get_llm_client() -> Optional[OpenAI]:
    api_key = (
        st.session_state.get("typed_api_key", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    base_url = (
        st.session_state.get("typed_base_url", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
    )

    if base_url and not base_url.rstrip("/").endswith("/v1"):
        base_url = f"{base_url.rstrip('/')}/v1"

    if not api_key:
        return None

    return OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        timeout=st.session_state.get("llm_timeout_seconds", 8),
    )


def get_llm_model() -> str:
    return (
        st.session_state.get("typed_model", "").strip()
        or os.getenv("GROQ_MODEL", "").strip()
        or os.getenv("GEMINI_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or "llama-3.1-8b-instant"
    )


def _alert_runtime() -> tuple[alerts.AlertState, alerts.AlertPolicy]:
    state = st.session_state.get("alert_state")
    if state is None:
        state = alerts.AlertState()
        st.session_state["alert_state"] = state
    policy = st.session_state.get("alert_policy")
    if policy is None:
        policy = alerts.AlertPolicy()
        st.session_state["alert_policy"] = policy
    return state, policy


def maybe_generate_coach_alert(metrics: BiomechanicsMetrics) -> None:
    poll_llm_future()

    alert_state, alert_policy = _alert_runtime()
    alerts.update_rolling(alert_state, metrics.tilt_score)

    confidence = float(st.session_state.get("face_tracking_quality", 0.0)) / 100.0
    if not scoring.confidence_gate(confidence, minimum=0.45):
        return

    decision = alerts.decide_alert(
        tilt=metrics.tilt_score,
        confidence=confidence,
        now=None,
        state=alert_state,
        policy=alert_policy,
        fallback_threshold=float(st.session_state.alert_threshold),
        recovery_active=bool(st.session_state.recovery_active),
        candidate_text=st.session_state.coach_alert,
    )
    if not decision.should_alert:
        return

    now = time.time()
    if not st.session_state.use_llm_alerts:
        st.session_state.coach_alert = build_local_coach_alert(metrics)
        start_recovery_window(metrics)
        st.session_state.last_local_alert_at = now
        alerts.commit_alert(
            state=alert_state,
            tilt=metrics.tilt_score,
            text=st.session_state.coach_alert,
            now=now,
        )
        return

    if now - st.session_state.last_llm_call_at < st.session_state.llm_cooldown_seconds:
        return

    config = get_llm_config()
    if config is None:
        st.session_state.coach_alert = "ADD API KEY FOR ALERTS"
        st.session_state.last_llm_call_at = now
        return

    future = st.session_state.get("llm_future")
    if future is not None and not future.done():
        st.session_state.coach_alert = "LLM IS PREPARING ONE COMMAND..."
        return

    payload = {
        "event": "tilt_risk",
        "coach_mode": st.session_state.coach_mode,
        "tilt_score": round(metrics.tilt_score, 1),
        "tilt_prediction": st.session_state.tilt_prediction,
        "somatic_fingerprint": build_somatic_fingerprint(),
        "biomechanics": {
            "shoulder_elevation_score": round(metrics.shoulder_elevation_score, 1),
            "shoulder_asymmetry_percent": round(metrics.shoulder_asymmetry_percent, 1),
            "shoulder_slope_degrees": round(metrics.shoulder_slope_degrees, 1),
            "jaw_clench_score": round(metrics.jaw_clench_score, 1),
            "emotion_primary": metrics.emotion_primary,
            "emotion_confidence": round(metrics.emotion_confidence, 1),
            "emotional_arousal_score": round(metrics.emotional_arousal_score, 1),
            "emotional_valence_score": round(metrics.emotional_valence_score, 1),
            "triggers": metrics.triggers,
        },
    }

    st.session_state.llm_future = get_llm_executor().submit(
        generate_llm_alert,
        config,
        payload,
        st.session_state.coach_mode,
    )
    st.session_state.llm_pending_started_at = now
    st.session_state.coach_alert = "LLM IS PREPARING ONE COMMAND..."
    start_recovery_window(metrics)
    st.session_state.last_llm_call_at = now
    alerts.commit_alert(
        state=alert_state,
        tilt=metrics.tilt_score,
        text=st.session_state.coach_alert,
        now=now,
    )


def get_llm_config() -> Optional[dict]:
    groq_key = (
        st.session_state.get("typed_groq_api_key", "").strip()
        or os.getenv("GROQ_API_KEY", "").strip()
    )
    if groq_key:
        return {
            "provider": "coach_router",
            "model": get_llm_model(),
            "timeout": st.session_state.get("llm_timeout_seconds", 8),
        }

    gemini_key = (
        st.session_state.get("typed_gemini_api_key", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )
    if gemini_key:
        return {
            "provider": "gemini",
            "api_key": gemini_key,
            "model": get_llm_model(),
            "timeout": st.session_state.get("llm_timeout_seconds", 8),
        }

    api_key = (
        st.session_state.get("typed_api_key", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    base_url = (
        st.session_state.get("typed_base_url", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
    )
    if base_url and not base_url.rstrip("/").endswith("/v1"):
        base_url = f"{base_url.rstrip('/')}/v1"
    if not api_key:
        return None
    return {
        "provider": "openai_compatible",
        "api_key": api_key,
        "base_url": base_url or None,
        "model": get_llm_model(),
        "timeout": st.session_state.get("llm_timeout_seconds", 8),
    }


@st.cache_data(ttl=10)
def cached_llm_health() -> dict:
    if product_intelligence is None:
        return {
            "status": "fallback",
            "connected": False,
            "latency_ms": None,
            "model": get_llm_model(),
            "message": 'Product intelligence module . Local fallback coach',
        }
    return product_intelligence.llm_health(timeout=1.2)


def generate_llm_alert(config: dict, payload: dict, coach_mode: str) -> str:
    if config.get("provider") == "coach_router":
        result = coach_providers.generate_coach_command(
            {
                "coach_mode": coach_mode,
                **(payload or {}),
            },
            timeout=float(config.get("timeout", 8)),
        )
        return sanitize_coach_command(result.get("command") or gemini_coach.fallback_command(payload))

    if config.get("provider") == "gemini":
        result = gemini_coach.generate_coach_command(
            {
                "coach_mode": coach_mode,
                **(payload or {}),
            },
            timeout=float(config.get("timeout", 8)),
        )
        return sanitize_coach_command(result.get("command") or gemini_coach.fallback_command(payload))

    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
        timeout=config["timeout"],
    )
    response = client.chat.completions.create(
        model=config["model"],
        messages=[
            {
                "role": "system",
                "content": (
                    LLM_SYSTEM_PROMPT
                    + " "
                    + COACH_MODE_PROMPTS.get(coach_mode, "")
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
        temperature=0.35,
        max_tokens=40,
    )
    command = response.choices[0].message.content or ""
    return sanitize_coach_command(command)


def poll_llm_future() -> None:
    future = st.session_state.get("llm_future")
    if future is None or not future.done():
        return
    try:
        st.session_state.coach_alert = sanitize_coach_command(future.result())
    except Exception as exc:
        st.session_state.coach_alert = f"LLM ERROR: {exc}"
    st.session_state.llm_future = None


def start_recovery_window(metrics: BiomechanicsMetrics) -> None:
    if st.session_state.recovery_active:
        return
    st.session_state.recovery_active = True
    st.session_state.recovery_started_at = time.time()
    st.session_state.recovery_start_tilt = float(metrics.tilt_score)
    st.session_state.recovery_score = 0.0
    st.session_state.recovery_seconds = None


def update_recovery_score(metrics: Optional[BiomechanicsMetrics]) -> None:
    if not st.session_state.recovery_active or metrics is None:
        return
    elapsed = time.time() - st.session_state.recovery_started_at
    start_tilt = max(float(st.session_state.recovery_start_tilt), 1.0)
    drop = max(start_tilt - float(metrics.tilt_score), 0.0)
    physical_release = clamp(
        (100 - metrics.shoulder_elevation_score) * 0.28
        + (100 - metrics.jaw_clench_score) * 0.25
        + (100 - metrics.emotional_arousal_score) * 0.22
        + drop / max(start_tilt, 1.0) * 100 * 0.25
    )
    speed_bonus = clamp((22 - elapsed) / 22 * 22)
    st.session_state.recovery_score = clamp(physical_release + speed_bonus)
    if st.session_state.recovery_score >= 72 or elapsed > 28:
        st.session_state.recovery_seconds = round(elapsed, 1)
        st.session_state.recovery_active = False
        recovery_runtime_state = st.session_state.get("alert_state")
        if recovery_runtime_state is not None:
            alerts.commit_recovery(state=recovery_runtime_state)


def build_local_coach_alert(metrics: BiomechanicsMetrics) -> str:
    """Instant local alert without waiting for cloud LLM."""
    if "jaw_clench" in metrics.triggers:
        return "UNCLENCH JAW. EXHALE."
    if "emotional_tension" in metrics.triggers:
        if metrics.emotion_primary in {"frustration", "anger"}:
            return "RESET FACE. ONE CLEAN BREATH."
        if metrics.emotion_primary in {"fatigue", "tired"}:
            return "BLINK. SOFTEN EYES. REFOCUS."
        return "RETURN FACE TO NEUTRAL. KEEP PLAYING."
    if "raised_shoulders" in metrics.triggers:
        return "DROP SHOULDERS. BREATHE."
    if "shoulder_asymmetry" in metrics.triggers:
        return "CENTER YOUR BODY. BACK TO FOCUS."
    return "RESET TENSION. NEXT FIGHT."


def build_auto_affective_label(metrics: BiomechanicsMetrics) -> dict:
    """English documentation cleaned for investor demo."""
    evidence = []
    if metrics.jaw_clench_score >= 55:
        evidence.append("jaw_clench")
    if metrics.brow_tension_score >= 48:
        evidence.append("brow_tension")
    if metrics.eye_focus_score >= 62:
        evidence.append("eye_focus")
    if metrics.mouth_tension_score >= 48:
        evidence.append("mouth_pressure")
    if metrics.shoulder_elevation_score >= 45:
        evidence.append("raised_shoulders")
    if metrics.shoulder_asymmetry_score >= 35:
        evidence.append("shoulder_asymmetry")
    voice_metrics = st.session_state.get("voice_metrics") or {}
    input_metrics = st.session_state.get("input_metrics") or {}
    if voice_metrics.get("voice_tension_score", 0) >= 58:
        evidence.append("voice_tension")
    if voice_metrics.get("speech_rate_proxy", 0) >= 58:
        evidence.append("speech_rate")
    if input_metrics.get("input_chaos_score", 0) >= 58:
        evidence.append('mouse/keyboard')

    if input_metrics.get("input_chaos_score", 0) >= 70 and metrics.emotional_arousal_score >= 45:
        label = "input_stress"
    elif voice_metrics.get("voice_tension_score", 0) >= 70 and metrics.emotional_arousal_score >= 45:
        label = "voice_stress"
    elif metrics.emotion_primary == "frustration" and metrics.emotional_arousal_score >= 55:
        label = "frustration"
    elif metrics.emotion_primary == "fatigue":
        label = "fatigue"
    elif metrics.emotion_primary == "surprise_stress":
        label = "stress_spike"
    elif metrics.emotion_primary == "focus" and metrics.emotional_arousal_score < 62:
        label = "focused_control"
    elif metrics.emotion_primary == "release":
        label = "recovery_release"
    elif metrics.emotion_primary == "calm_control":
        label = "calm_control"
    else:
        label = metrics.emotion_primary

    confidence = clamp(
        metrics.emotion_confidence * 0.48
        + st.session_state.face_tracking_quality * 0.22
        + min(len(evidence), 4) * 7.5
    )
    if metrics.emotion_primary == "no_face":
        confidence = 0.0
        evidence = ["no_face"]

    return {
        "label": label,
        "source": "auto_mediapipe_blendshape_v0",
        "confidence": round(confidence, 2),
        "evidence": evidence,
    }


def metric_feature_vector(metrics: BiomechanicsMetrics) -> dict[str, float]:
    features = {
        "tilt_score": float(metrics.tilt_score),
        "raw_stress_score": float(metrics.raw_stress_score),
        "shoulder_elevation_score": float(metrics.shoulder_elevation_score),
        "shoulder_asymmetry_score": float(metrics.shoulder_asymmetry_score),
        "shoulder_asymmetry_percent": float(metrics.shoulder_asymmetry_percent),
        "shoulder_slope_degrees": float(metrics.shoulder_slope_degrees),
        "jaw_clench_score": float(metrics.jaw_clench_score),
        "jaw_blendshape_score": float(metrics.jaw_blendshape_score),
        "brow_tension_score": float(metrics.brow_tension_score),
        "eye_focus_score": float(metrics.eye_focus_score),
        "mouth_tension_score": float(metrics.mouth_tension_score),
        "emotional_arousal_score": float(metrics.emotional_arousal_score),
        "emotional_valence_score": float(metrics.emotional_valence_score),
        "face_tracking_quality": float(st.session_state.face_tracking_quality),
    }
    voice = st.session_state.get("voice_metrics") or {}
    input_metrics = st.session_state.get("input_metrics") or {}
    for key in (
        "voice_rms",
        "voice_pitch_proxy",
        "voice_tension_score",
        "speech_rate_proxy",
        "key_rate_5s",
        "click_rate_5s",
        "mouse_speed_proxy",
        "input_chaos_score",
        "recovery_score",
    ):
        if key in voice:
            features[key] = float(voice.get(key, 0.0))
        elif key in input_metrics:
            features[key] = float(input_metrics.get(key, 0.0))
        elif key == "recovery_score":
            features[key] = float(st.session_state.recovery_score)
    return features


def append_affective_dataset_sample(metrics: BiomechanicsMetrics) -> None:
    if not st.session_state.auto_dataset_enabled:
        return
    now = time.time()
    last_event = st.session_state.last_game_event
    if now - st.session_state.last_dataset_write_at < 0.75:
        return
    st.session_state.last_dataset_write_at = now

    auto_label = build_auto_affective_label(metrics)
    if auto_label["confidence"] < 35:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sample = {
        "schema_version": "kinaesthetic_affective_somatic_v0",
        "session_id": st.session_state.session_id,
        "timestamp": metrics.timestamp,
        "auto_label": auto_label,
        "features": metric_feature_vector(metrics),
        "emotion_scores": {
            key: round(float(value), 2)
            for key, value in metrics.emotion_scores.items()
        },
        "voice_metrics": st.session_state.get("voice_metrics") or {},
        "input_metrics": st.session_state.get("input_metrics") or {},
        "recovery": {
            "active": bool(st.session_state.recovery_active),
            "score": round(float(st.session_state.recovery_score), 2),
            "seconds": st.session_state.recovery_seconds,
        },
        "triggers": list(metrics.triggers),
        "game_event": last_event,
        "privacy": {
            "video_saved": False,
            "landmarks_only": bool(st.session_state.landmark_only_logging),
        },
    }
    with AFFECTIVE_DATASET_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(sample, ensure_ascii=False) + "\n")
    st.session_state.dataset_samples_written += 1


def train_somatic_proto_model() -> dict:
    """English documentation cleaned for investor demo."""
    if not AFFECTIVE_DATASET_PATH.exists():
        status = ': auto-label samples'
        st.session_state.somatic_model_status = status
        return {"ok": False, "status": status}

    groups: dict[str, list[dict[str, float]]] = {}
    with AFFECTIVE_DATASET_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                sample = json.loads(line)
            except json.JSONDecodeError:
                continue
            label = sample.get("auto_label", {}).get("label")
            confidence = float(sample.get("auto_label", {}).get("confidence", 0.0))
            features = sample.get("features") or {}
            if not label or confidence < 45 or not features:
                continue
            groups.setdefault(label, []).append(features)

    if len(groups) < 2:
        status = '2 auto-label proto-'
        st.session_state.somatic_model_status = status
        return {"ok": False, "status": status}

    feature_names = sorted(next(iter(next(iter(groups.values())))).keys())
    centroids = {}
    counts = {}
    for label, rows in groups.items():
        matrix = np.array(
            [[float(row.get(name, 0.0)) for name in feature_names] for row in rows],
            dtype=float,
        )
        centroids[label] = np.mean(matrix, axis=0).round(4).tolist()
        counts[label] = len(rows)

    model = {
        "model_type": "nearest_centroid_proto_v0",
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "feature_names": feature_names,
        "centroids": centroids,
        "counts": counts,
    }
    SOMATIC_MODEL_PATH.write_text(
        json.dumps(model, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    st.session_state.somatic_model = model
    status = f"Proto-model trained: {len(centroids)} labels, {sum(counts.values())} samples"
    st.session_state.somatic_model_status = status
    return {"ok": True, "status": status}


def load_somatic_proto_model() -> Optional[dict]:
    if st.session_state.somatic_model:
        return st.session_state.somatic_model
    if not SOMATIC_MODEL_PATH.exists():
        return None
    try:
        model = json.loads(SOMATIC_MODEL_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    st.session_state.somatic_model = model
    st.session_state.somatic_model_status = (
        f"Proto-model loaded: {len(model.get('centroids', {}))} labels"
    )
    return model


def predict_somatic_proto_state(metrics: BiomechanicsMetrics) -> Optional[dict]:
    model = load_somatic_proto_model()
    if not model:
        return None
    feature_names = model.get("feature_names", [])
    features = metric_feature_vector(metrics)
    vector = np.array([float(features.get(name, 0.0)) for name in feature_names], dtype=float)
    distances = []
    for label, centroid in model.get("centroids", {}).items():
        centroid_vector = np.array(centroid, dtype=float)
        if centroid_vector.shape != vector.shape:
            continue
        distance = float(np.linalg.norm((vector - centroid_vector) / 100.0))
        distances.append((label, distance))
    if not distances:
        return None
    distances.sort(key=lambda item: item[1])
    label, distance = distances[0]
    confidence = clamp(100 - distance * 58)
    return {
        "label": label,
        "confidence": round(confidence, 2),
        "distance": round(distance, 4),
        "model_type": model.get("model_type", "unknown"),
    }


def check_openface_bridge() -> dict:
    """English documentation cleaned for investor demo."""
    candidates = [
        st.session_state.get("openface_executable", "").strip(),
        str(APP_ROOT / "OpenFace" / "FeatureExtraction.exe"),
        str(APP_ROOT / "openface" / "FeatureExtraction.exe"),
        shutil.which("FeatureExtraction.exe") or "",
        shutil.which("FeatureExtraction") or "",
    ]
    executable = next((path for path in candidates if path and Path(path).exists()), "")
    if executable:
        status = f"OpenFace bridge      : {executable}"
        result = {
            "ready": True,
            "executable": executable,
            "mode": "offline_action_units",
            "status": status,
        }
    else:
        status = (
            'OpenFace . AU-verifier FeatureExtraction.exe'
            'C:\\Users\\user\\Desktop\\MeirX\\OpenFace\\'
        )
        result = {
            "ready": False,
            "executable": "",
            "mode": "mediapipe_blendshapes_only",
            "status": status,
        }
    st.session_state.openface_status = status
    return result


def research_emotion_backend_status() -> dict:
    """English documentation cleaned for investor demo."""
    deepface_available = importlib.util.find_spec("deepface") is not None
    return {
        "realtime": "MediaPipe Face Landmarker: 478 face landmarks + 52 blendshapes",
        "au_verifier": st.session_state.openface_status,
        "deepface_available": deepface_available,
        "research_status": (
            'DeepFace'
            if deepface_available
            else 'DeepFace/AffectNet : offline research backend'
        ),
    }


def safe_profile_id() -> str:
    raw = st.session_state.get("user_profile_id", "default_player").strip() or "default_player"
    return "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in raw)[:64]


def profile_path() -> Path:
    return PROFILES_DIR / f"{safe_profile_id()}.json"


def somatic_twin_path() -> Path:
    return PROFILES_DIR / f"{safe_profile_id()}_somatic_twin.json"


def save_personal_profile() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profile = {
        "profile_id": safe_profile_id(),
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "baseline_profile": st.session_state.baseline_profile,
        "jaw_relaxed_baseline": st.session_state.jaw_relaxed_baseline,
        "jaw_clenched_baseline": st.session_state.jaw_clenched_baseline,
        "somatic_model_path": str(SOMATIC_MODEL_PATH),
        "notes": 'baseline calibration',
    }
    profile_path().write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    st.session_state.profile_status = f"Profile saved: {profile_path()}"


def load_personal_profile() -> None:
    path = profile_path()
    if not path.exists():
        st.session_state.profile_status = f"Profile not found: {path}"
        return
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        st.session_state.profile_status = ': JSON'
        return
    st.session_state.baseline_profile = profile.get("baseline_profile")
    st.session_state.jaw_relaxed_baseline = profile.get("jaw_relaxed_baseline")
    st.session_state.jaw_clenched_baseline = profile.get("jaw_clenched_baseline")
    st.session_state.profile_status = f"Profile loaded: {path}"


def build_somatic_signature() -> dict:
    series = list(st.session_state.session_series)
    if not series:
        return {
            "status": "collecting signal",
            "signature_hash": "pending",
            "dominant_lock": "n/a",
            "stress_entry_speed": 0.0,
            "recovery_half_life": None,
            "resilience_score": 0.0,
            "volatility": 0.0,
            "modality_weights": {},
        }

    tilts = np.array([float(row.get("tilt", 0.0)) for row in series], dtype=float)
    arousals = np.array([float(row.get("arousal", 0.0)) for row in series], dtype=float)
    jaws = np.array([float(row.get("jaw", 0.0)) for row in series], dtype=float)
    shoulders = np.array([float(row.get("shoulders", 0.0)) for row in series], dtype=float)
    inputs = np.array([float(row.get("input", 0.0)) for row in series], dtype=float)
    voices = np.array([float(row.get("voice", 0.0)) for row in series], dtype=float)
    times = np.array([float(row.get("epoch", index)) for index, row in enumerate(series)], dtype=float)
    times = times - times[0]

    peak_index = int(np.argmax(tilts))
    pre_start = max(peak_index - 16, 0)
    pre_tilt = tilts[pre_start : peak_index + 1]
    if len(pre_tilt) >= 3:
        stress_entry_speed = float(np.polyfit(np.arange(len(pre_tilt)), pre_tilt, 1)[0])
    else:
        stress_entry_speed = 0.0

    peak_tilt = float(np.max(tilts))
    final_tilt = float(tilts[-1])
    half_target = final_tilt + (peak_tilt - final_tilt) * 0.5
    recovery_half_life = None
    for index in range(peak_index, len(tilts)):
        if tilts[index] <= half_target:
            recovery_half_life = round(float(times[index] - times[peak_index]), 1)
            break

    modality_weights = {
        "jaw": float(np.mean(jaws[-45:])) if len(jaws) else 0.0,
        "shoulders": float(np.mean(shoulders[-45:])) if len(shoulders) else 0.0,
        "arousal": float(np.mean(arousals[-45:])) if len(arousals) else 0.0,
        "input": float(np.mean(inputs[-45:])) if len(inputs) else 0.0,
        "voice": float(np.mean(voices[-45:])) if len(voices) else 0.0,
    }
    dominant_lock = max(modality_weights, key=modality_weights.get)
    volatility = float(np.std(tilts))
    command = coach_command_effectiveness()
    resilience_score = clamp(
        100
        - float(np.mean(tilts)) * 0.34
        - volatility * 0.72
        + float(command.get("effectiveness", 0.0)) * 0.42
        + float(st.session_state.recovery_score) * 0.28
    )
    signature_payload = {
        "profile": safe_profile_id(),
        "dominant_lock": dominant_lock,
        "entry": round(stress_entry_speed, 3),
        "half": recovery_half_life,
        "resilience": round(resilience_score, 2),
        "weights": {key: round(value, 2) for key, value in modality_weights.items()},
    }
    signature_hash = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "status": "ready",
        "signature_hash": signature_hash,
        "dominant_lock": dominant_lock,
        "stress_entry_speed": round(stress_entry_speed, 2),
        "recovery_half_life": recovery_half_life,
        "resilience_score": round(resilience_score, 1),
        "volatility": round(volatility, 1),
        "modality_weights": {key: round(value, 1) for key, value in modality_weights.items()},
    }


def load_somatic_twin_memory() -> dict:
    if st.session_state.somatic_twin_memory:
        return st.session_state.somatic_twin_memory
    path = somatic_twin_path()
    if path.exists():
        try:
            memory = json.loads(path.read_text(encoding="utf-8"))
            st.session_state.somatic_twin_memory = memory
            st.session_state.somatic_twin_status = f"Twin memory saved: {path}"
            return memory
        except json.JSONDecodeError:
            pass
    memory = {
        "profile_id": safe_profile_id(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "sessions": 0,
        "signature_history": [],
        "cue_memory": {},
        "driver_memory": {},
    }
    st.session_state.somatic_twin_memory = memory
    return memory


def update_somatic_twin_memory() -> dict:
    memory = load_somatic_twin_memory()
    signature = build_somatic_signature()
    report = build_post_match_report()
    command = coach_command_effectiveness()
    protocol = generate_recovery_protocol(report)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    memory["profile_id"] = safe_profile_id()
    memory["updated_at"] = now_iso
    memory["sessions"] = int(memory.get("sessions", 0)) + 1
    memory.setdefault("signature_history", []).append(
        {
            "timestamp": now_iso,
            "session_id": st.session_state.session_id,
            "signature": signature,
            "report": {
                "what_broke_state": report.get("what_broke_state"),
                "dominant_emotion": report.get("dominant_emotion"),
                "recovery_score": report.get("recovery_score"),
            },
        }
    )
    memory["signature_history"] = memory["signature_history"][-24:]

    cue = command.get("best_command") or protocol["cue"]
    if cue and cue != "n/a":
        cue_stats = memory.setdefault("cue_memory", {}).setdefault(
            cue,
            {
                "trials": 0,
                "avg_effectiveness": 0.0,
                "best_driver": report.get("what_broke_state", "n/a"),
                "last_seen": now_iso,
            },
        )
        trials = int(cue_stats.get("trials", 0))
        old_avg = float(cue_stats.get("avg_effectiveness", 0.0))
        new_effect = float(command.get("effectiveness", 0.0))
        cue_stats["trials"] = trials + 1
        cue_stats["avg_effectiveness"] = round((old_avg * trials + new_effect) / max(trials + 1, 1), 2)
        cue_stats["best_driver"] = report.get("what_broke_state", cue_stats.get("best_driver", "n/a"))
        cue_stats["last_seen"] = now_iso

    driver = report.get("what_broke_state", "n/a")
    driver_stats = memory.setdefault("driver_memory", {}).setdefault(
        driver,
        {"count": 0, "avg_recovery": 0.0, "last_protocol": protocol["name"]},
    )
    count = int(driver_stats.get("count", 0))
    old_recovery = float(driver_stats.get("avg_recovery", 0.0))
    new_recovery = float(report.get("recovery_score", 0.0))
    driver_stats["count"] = count + 1
    driver_stats["avg_recovery"] = round((old_recovery * count + new_recovery) / max(count + 1, 1), 2)
    driver_stats["last_protocol"] = protocol["name"]

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    somatic_twin_path().write_text(
        json.dumps(memory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    st.session_state.somatic_twin_memory = memory
    st.session_state.somatic_twin_status = f"Twin memory saved: {somatic_twin_path()}"
    st.session_state.next_best_intervention = recommend_next_best_intervention(memory)
    return memory


def recommend_next_best_intervention(memory: Optional[dict] = None) -> dict:
    memory = memory or load_somatic_twin_memory()
    report = build_post_match_report()
    protocol = generate_recovery_protocol(report)
    driver = report.get("what_broke_state", "n/a")
    cue_memory = memory.get("cue_memory", {})
    best_cue = None
    best_score = -1.0
    for cue, stats in cue_memory.items():
        score = float(stats.get("avg_effectiveness", 0.0))
        if stats.get("best_driver") == driver:
            score += 12
        if int(stats.get("trials", 0)) >= 2:
            score += 5
        if score > best_score:
            best_score = score
            best_cue = cue
    if best_cue:
        recommendation = {
            "source": "personal_twin_memory",
            "cue": best_cue,
            "expected_effectiveness": round(max(best_score, 0.0), 1),
            "driver": driver,
            "reason": "matched personal cue history",
        }
    else:
        recommendation = {
            "source": "protocol_prior",
            "cue": protocol["cue"],
            "expected_effectiveness": 48.0,
            "driver": driver,
            "reason": ',',
        }
    st.session_state.next_best_intervention = recommendation
    return recommendation


def somatic_tokens_from_row(row: dict) -> list[str]:
    """English documentation cleaned for investor demo."""
    tokens = []
    tilt = float(row.get("tilt", 0.0))
    jaw = float(row.get("jaw", 0.0))
    shoulders = float(row.get("shoulders", 0.0))
    arousal = float(row.get("arousal", 0.0))
    input_chaos = float(row.get("input", 0.0))
    voice = float(row.get("voice", 0.0))
    recovery = float(row.get("recovery", 0.0))
    emotion = str(row.get("emotion", "")).strip()
    event = str(row.get("event", "")).strip()

    if event:
        tokens.append(f"EVT_{event.upper()}")
    if tilt >= 80:
        tokens.append("TILT_RED")
    elif tilt >= 50:
        tokens.append("TILT_AMBER")
    else:
        tokens.append("TILT_GREEN")
    if jaw >= 65:
        tokens.append("JAW_LOCK")
    elif jaw >= 35:
        tokens.append("JAW_TENSE")
    if shoulders >= 65:
        tokens.append("SHOULDER_GUARD")
    elif shoulders >= 35:
        tokens.append("SHOULDER_RISE")
    if arousal >= 70:
        tokens.append("AROUSAL_SPIKE")
    elif arousal >= 45:
        tokens.append("AROUSAL_BUILD")
    if input_chaos >= 65:
        tokens.append("INPUT_SPAM")
    elif input_chaos >= 35:
        tokens.append("INPUT_NOISE")
    if voice >= 65:
        tokens.append("VOICE_LOCK")
    elif voice >= 35:
        tokens.append("VOICE_TENSE")
    if recovery >= 70:
        tokens.append("RECOVERY_REGAIN")
    elif recovery >= 35:
        tokens.append("RECOVERY_START")
    if "frustration" in emotion:
        tokens.append("EMO_FRUSTRATION")
    elif "fatigue" in emotion:
        tokens.append("EMO_FATIGUE")
    elif "focus" in emotion:
        tokens.append("EMO_FOCUS")
    elif "calm_control" in emotion or "control" in emotion:
        tokens.append("EMO_CONTROL")
    elif "stress" in emotion:
        tokens.append("EMO_STRESS")
    if not tokens:
        tokens.append("NEUTRAL_SCAN")
    return tokens


def build_somatic_language_engine() -> dict:
    """English documentation cleaned for investor demo."""
    series = list(st.session_state.session_series)
    if len(series) < 3:
        return {
            "status": "collecting signal",
            "alphabet_size": 0,
            "entropy": 0.0,
            "sentence": "WAIT_FOR_SIGNAL",
            "current_phrase": "n/a",
            "next_token": "n/a",
            "next_confidence": 0.0,
            "rare_transition_score": 0.0,
            "token_counts": {},
            "state_space": None,
        }

    frame_phrases = []
    token_stream = []
    for row in series:
        tokens = somatic_tokens_from_row(row)
        token_stream.extend(tokens)
        phrase = "+".join(tokens[:4])
        if not frame_phrases or frame_phrases[-1] != phrase:
            frame_phrases.append(phrase)

    token_counts = {}
    for token in token_stream:
        token_counts[token] = token_counts.get(token, 0) + 1

    total = max(sum(token_counts.values()), 1)
    entropy = 0.0
    for count in token_counts.values():
        probability = count / total
        entropy -= probability * math.log2(max(probability, 1e-9))
    max_entropy = math.log2(max(len(token_counts), 2))
    normalized_entropy = clamp(entropy / max_entropy * 100)

    bigrams = {}
    for left, right in zip(frame_phrases, frame_phrases[1:]):
        bigrams.setdefault(left, {})
        bigrams[left][right] = bigrams[left].get(right, 0) + 1
    current_phrase = frame_phrases[-1]
    next_options = bigrams.get(current_phrase, {})
    if next_options:
        next_token, next_count = max(next_options.items(), key=lambda item: item[1])
        next_confidence = clamp(next_count / max(sum(next_options.values()), 1) * 100)
    else:
        ranked_tokens = sorted(token_counts.items(), key=lambda item: item[1], reverse=True)
        next_token = ranked_tokens[0][0] if ranked_tokens else "n/a"
        next_confidence = 18.0 if ranked_tokens else 0.0

    transition_counts = [
        bigrams.get(left, {}).get(right, 0)
        for left, right in zip(frame_phrases, frame_phrases[1:])
    ]
    rare_transition_score = clamp(
        (1.0 - (float(np.mean(transition_counts)) / max(max(transition_counts), 1)))
        * 100
        if transition_counts
        else 0.0
    )

    feature_rows = []
    for row in series:
        feature_rows.append(
            [
                float(row.get("tilt", 0.0)),
                float(row.get("arousal", 0.0)),
                float(row.get("jaw", 0.0)),
                float(row.get("shoulders", 0.0)),
                float(row.get("input", 0.0)),
                float(row.get("voice", 0.0)),
                float(row.get("recovery", 0.0)),
            ]
        )
    matrix = np.array(feature_rows, dtype=float)
    state_space = None
    if len(matrix) >= 3:
        centered = matrix - np.mean(matrix, axis=0)
        try:
            _, _, vt = np.linalg.svd(centered, full_matrices=False)
            coords = centered @ vt[:2].T
            state_space = pd.DataFrame(
                {
                    "Somatic X": coords[:, 0],
                    "Somatic Y": coords[:, 1],
                    "Tilt": [row.get("tilt", 0.0) for row in series],
                }
            )
        except np.linalg.LinAlgError:
            state_space = None

    return {
        "status": "somatic language ready",
        "alphabet_size": len(token_counts),
        "entropy": round(normalized_entropy, 1),
        "sentence": " -> ".join(frame_phrases[-10:]),
        "current_phrase": current_phrase,
        "next_token": next_token,
        "next_confidence": round(next_confidence, 1),
        "rare_transition_score": round(rare_transition_score, 1),
        "token_counts": dict(sorted(token_counts.items(), key=lambda item: item[1], reverse=True)[:12]),
        "state_space": state_space,
    }


def build_universal_readiness_score(metrics: Optional[BiomechanicsMetrics] = None) -> dict:
    metrics = metrics or st.session_state.current_metrics
    latest = list(st.session_state.session_series)[-1:] or [{}]
    row = latest[0]
    if metrics:
        stress = clamp(
            metrics.emotional_arousal_score * 0.34
            + metrics.jaw_clench_score * 0.22
            + metrics.shoulder_elevation_score * 0.18
            + float((st.session_state.get("input_metrics") or {}).get("input_chaos_score", 0.0)) * 0.14
            + float((st.session_state.get("voice_metrics") or {}).get("voice_tension_score", 0.0)) * 0.12
        )
        focus = clamp(metrics.eye_focus_score * 0.45 + (100 - metrics.emotional_arousal_score) * 0.25 + metrics.emotional_valence_score * 0.30)
        fatigue = clamp((100 - metrics.emotional_valence_score) * 0.28 + metrics.brow_tension_score * 0.22 + max(0, 100 - metrics.eye_focus_score) * 0.24 + metrics.jaw_clench_score * 0.26)
        control = clamp(100 - stress * 0.52 - float(st.session_state.tilt_score) * 0.28 + st.session_state.recovery_score * 0.30)
    else:
        stress = float(row.get("arousal", 0.0))
        focus = clamp(100 - stress + float(row.get("recovery", 0.0)) * 0.45)
        fatigue = clamp(stress * 0.42 + float(row.get("jaw", 0.0)) * 0.18)
        control = clamp(100 - float(row.get("tilt", 0.0)) * 0.55 + float(row.get("recovery", 0.0)) * 0.35)
    recovery = clamp(float(st.session_state.recovery_score) if st.session_state.recovery_score else float(row.get("recovery", 0.0)))
    readiness = clamp(focus * 0.28 + control * 0.28 + recovery * 0.18 + (100 - stress) * 0.18 + (100 - fatigue) * 0.08)
    if readiness >= 76:
        label = "ready"
    elif readiness >= 52:
        label = "watch"
    elif readiness >= 32:
        label = 'reset'
    else:
        label = "high risk"
    return {
        "readiness": round(readiness, 1),
        "focus": round(focus, 1),
        "stress": round(stress, 1),
        "recovery": round(recovery, 1),
        "fatigue": round(fatigue, 1),
        "control": round(control, 1),
        "label": label,
    }


def body_state_embedding(metrics: BiomechanicsMetrics) -> list[float]:
    features = metric_feature_vector(metrics)
    keys = [
        "tilt_score",
        "raw_stress_score",
        "shoulder_elevation_score",
        "shoulder_asymmetry_score",
        "jaw_clench_score",
        "brow_tension_score",
        "eye_focus_score",
        "mouth_tension_score",
        "emotional_arousal_score",
        "emotional_valence_score",
        "voice_tension_score",
        "speech_rate_proxy",
        "input_chaos_score",
        "recovery_score",
    ]
    vector = np.array([float(features.get(key, 0.0)) / 100.0 for key in keys], dtype=float)
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector = vector / norm
    return vector.round(5).tolist()


def append_body_state_embedding(metrics: BiomechanicsMetrics) -> None:
    now = time.time()
    if now - st.session_state.last_embedding_write_at < 1.0:
        return
    st.session_state.last_embedding_write_at = now
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    language = build_somatic_language_engine()
    record = {
        "timestamp": metrics.timestamp,
        "session_id": st.session_state.session_id,
        "profile_id": safe_profile_id(),
        "embedding_model": "kinaesthetic_local_feature_embedding_v0",
        "embedding": body_state_embedding(metrics),
        "tokens": somatic_tokens_from_row((list(st.session_state.session_series)[-1] if st.session_state.session_series else {})),
        "emotion": metrics.emotion_primary,
        "tilt": round(metrics.tilt_score, 1),
        "current_phrase": language.get("current_phrase", "n/a"),
    }
    with BODY_STATE_EMBEDDINGS_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_similar_body_states(metrics: Optional[BiomechanicsMetrics] = None, limit: int = 5) -> list[dict]:
    if metrics is None or not BODY_STATE_EMBEDDINGS_PATH.exists():
        return []
    query = np.array(body_state_embedding(metrics), dtype=float)
    results = []
    with BODY_STATE_EMBEDDINGS_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            vector = np.array(record.get("embedding", []), dtype=float)
            if vector.shape != query.shape:
                continue
            similarity = float(np.dot(query, vector))
            results.append(
                {
                    "similarity": round(similarity * 100, 1),
                    "timestamp": record.get("timestamp", "n/a"),
                    "emotion": record.get("emotion", "n/a"),
                    "tilt": record.get("tilt", 0),
                    "phrase": record.get("current_phrase", "n/a"),
                }
            )
    results.sort(key=lambda item: item["similarity"], reverse=True)
    return results[:limit]


def body_state_embedding_stats(metrics: Optional[BiomechanicsMetrics] = None) -> dict:
    if not BODY_STATE_EMBEDDINGS_PATH.exists():
        return {
            "count": 0,
            "model": "kinaesthetic_local_feature_embedding_v0",
            "similar": [],
            "last_phrase": "n/a",
        }
    count = 0
    last_record = {}
    with BODY_STATE_EMBEDDINGS_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                last_record = json.loads(line)
            except json.JSONDecodeError:
                continue
            count += 1
    return {
        "count": count,
        "model": last_record.get("embedding_model", "kinaesthetic_local_feature_embedding_v0"),
        "similar": find_similar_body_states(metrics, limit=4) if metrics else [],
        "last_phrase": last_record.get("current_phrase", "n/a"),
    }


def maybe_run_somatic_autopilot(metrics: BiomechanicsMetrics) -> None:
    if not st.session_state.autopilot_enabled:
        st.session_state.autopilot_status = 'Autopilot'
        return
    now = time.time()
    if now - st.session_state.last_autopilot_at < 12:
        return
    prediction = st.session_state.tilt_prediction or {}
    language = build_somatic_language_engine()
    risky_next = str(language.get("next_token", ""))
    readiness = build_universal_readiness_score(metrics)
    imminent = (
        prediction.get("seconds") is not None
        and float(prediction.get("seconds", 999)) <= 22
        and float(prediction.get("confidence", 0)) >= 45
    )
    token_risk = any(token in risky_next for token in ("JAW_LOCK", "TILT_RED", "AROUSAL_SPIKE", "INPUT_SPAM", "SHOULDER_GUARD"))
    readiness_risk = readiness["readiness"] < 54 and metrics.tilt_score < st.session_state.alert_threshold
    if not (imminent or token_risk or readiness_risk):
        st.session_state.autopilot_status = 'Autopilot : intervention'
        return
    report = build_post_match_report()
    protocol = generate_recovery_protocol(report)
    cue = protocol["cue"]
    if metrics.jaw_clench_score >= 42 or "JAW" in risky_next:
        cue = 'reset'
    elif metrics.shoulder_elevation_score >= 42 or "SHOULDER" in risky_next:
        cue = 'reset'
    elif float((st.session_state.get("input_metrics") or {}).get("input_chaos_score", 0.0)) >= 42 or "INPUT" in risky_next:
        cue = 'reset'
    st.session_state.autopilot_cue = cue
    st.session_state.coach_alert = cue
    st.session_state.last_autopilot_at = now
    st.session_state.autopilot_status = (
        f"Autopilot cue armed: readiness {readiness['readiness']:.0f}%, next token {risky_next}"
    )
    start_recovery_window(metrics)


def search_somatic_moments(query: str, limit: int = 8) -> list[dict]:
    query = (query or "").strip().lower()
    if not query:
        return []
    terms = {
        "jaw": ["jaw", "clench", "teeth"],
        "death": ["death", "round loss"],
        "clutch": ["clutch", "pressure"],
        "toxic": ["toxic", "chat"],
        "recovery": ["recovery", "reset"],
        "tilt": ["tilt", "risk"],
        "voice": ["voice", "comms"],
        "input": ["input", "spam", "mouse", "keyboard"],
    }
    active = {name for name, aliases in terms.items() if any(alias in query for alias in aliases)}
    rows = list(st.session_state.session_series)
    results = []
    for row in rows:
        score = 0
        if "jaw" in active and float(row.get("jaw", 0)) >= 45:
            score += float(row.get("jaw", 0))
        if "death" in active and row.get("event") == "death":
            score += 90
        if "clutch" in active and row.get("event") == "clutch":
            score += 90
        if "toxic" in active and row.get("event") == "toxic_chat":
            score += 90
        if "recovery" in active and float(row.get("recovery", 0)) >= 55:
            score += float(row.get("recovery", 0))
        if "tilt" in active and float(row.get("tilt", 0)) >= 70:
            score += float(row.get("tilt", 0))
        if "voice" in active and float(row.get("voice", 0)) >= 45:
            score += float(row.get("voice", 0))
        if "input" in active and float(row.get("input", 0)) >= 45:
            score += float(row.get("input", 0))
        if score:
            results.append(
                {
                    "score": round(score, 1),
                    "time": row.get("t", "n/a"),
                    "event": row.get("event", ""),
                    "emotion": row.get("emotion", "n/a"),
                    "tilt": row.get("tilt", 0),
                    "jaw": row.get("jaw", 0),
                    "recovery": row.get("recovery", 0),
                    "phrase": "+".join(somatic_tokens_from_row(row)[:4]),
                }
            )
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


def answer_coach_copilot(question: str) -> str:
    question = (question or "").strip()
    if not question:
        return "Ask about breakdown, clutch prep, recovery, or which command worked."
    report = build_post_match_report()
    command = coach_command_effectiveness()
    causal = build_causal_intervention_graph()
    readiness = build_universal_readiness_score()
    next_best = recommend_next_best_intervention()
    q = question.lower()
    if "why" in q or "breakdown" in q:
        return (
            f"Main breakdown driver: {report.get('what_broke_state', 'n/a')}. "
            f"Trigger event: {report.get('trigger_event', 'n/a')}. "
            f"Dominant state: {report.get('dominant_emotion', 'n/a')}."
        )
    if "clutch" in q or "pressure" in q:
        return (
            f"Before clutch, aim for readiness above 70. Current readiness is {readiness['readiness']:.0f}. "
            f"Next cue: {next_best.get('cue', 'n/a')}."
        )
    if "command" in q or "worked" in q:
        return (
            f"Best command: {command.get('best_command', 'n/a')}. "
            f"Effectiveness: {command.get('effectiveness', 0):.0f}%, tilt drop {command.get('tilt_drop', 0):.0f}."
        )
    if "train" in q or "practice" in q:
        training = generate_micro_training_plan()
        return f"3-minute drill: {training['title']}. {training['summary']}"
    return (
        f"Current state: readiness {readiness['readiness']:.0f} ({readiness['label']}), "
        f"causal confidence {causal.get('causal_confidence', 0):.0f}%, "
        f"next cue: {next_best.get('cue', 'n/a')}."
    )


def detect_twin_drift() -> dict:
    memory = load_somatic_twin_memory()
    history = memory.get("signature_history", [])
    current = build_somatic_signature()
    if len(history) < 2 or current.get("status") != "ready":
        return {
            "status": "collecting baseline",
            "drift_score": 0.0,
            "label": "baseline forming",
            "reason": 'Somatic Twin Memory',
        }
    current_weights = current.get("modality_weights", {})
    historical = []
    for item in history[-10:]:
        weights = item.get("signature", {}).get("modality_weights", {})
        if weights:
            historical.append(weights)
    if not historical:
        return {"status": "collecting baseline", "drift_score": 0.0, "label": "pending", "reason": "not enough signatures"}
    keys = sorted(set(current_weights) | {key for weights in historical for key in weights})
    current_vec = np.array([float(current_weights.get(key, 0.0)) for key in keys], dtype=float)
    hist_vec = np.mean(
        np.array([[float(weights.get(key, 0.0)) for key in keys] for weights in historical], dtype=float),
        axis=0,
    )
    drift = float(np.linalg.norm(current_vec - hist_vec) / max(math.sqrt(len(keys)) * 100, 1) * 100)
    if drift >= 34:
        label = "meaningful drift"
        reason = 'baseline calibration'
    elif drift >= 18:
        label = "watch drift"
        reason = "body-state pattern moved from baseline"
    else:
        label = 'baseline calibration'
        reason = "near personal baseline"
    return {
        "status": "ready",
        "drift_score": round(clamp(drift), 1),
        "label": label,
        "reason": reason,
    }


def generate_micro_training_plan() -> dict:
    report = build_post_match_report()
    protocol = generate_recovery_protocol(report)
    driver = report.get("what_broke_state", "n/a")
    blocks = {
        "jaw": ["0:00-0:40 jaw unlock", "0:40-1:40 tongue floor + nasal exhale", "1:40-3:00 soft gaze while jaw stays open"],
        "shoulders": ["0:00-0:40 shoulder drop", "0:40-1:40 scapula heavy breathing", "1:40-3:00 game posture reset"],
        "input": ["0:00-0:40 hands off keys", "0:40-1:40 slow click drill", "1:40-3:00 calm aim tracing"],
        "voice": ["0:00-0:40 lower tone", "0:40-1:40 short comms only", "1:40-3:00 silent recovery breath"],
        "face": ["0:00-0:40 face neutral", "0:40-1:40 peripheral vision", "1:40-3:00 slow exhale before next fight"],
    }
    chosen = blocks.get(driver, ["0:00-1:00 shoulders down", "1:00-2:00 jaw soft", "2:00-3:00 long exhale"])
    return {
        "title": f"3-min {protocol['name']}",
        "driver": driver,
        "cue": protocol["cue"],
        "blocks": chosen,
        "summary": " / ".join(chosen),
    }


def build_team_synchrony_model() -> dict:
    current = float(st.session_state.tilt_score)
    series = list(st.session_state.session_series)
    base = current if current else (float(series[-1].get("tilt", 0)) if series else 18.0)
    roster = {
        "you": base,
        "duo": clamp(base * 0.68 + 11),
        "igl": clamp(base * 0.42 + 22),
        "support": clamp(base * 0.36 + 18),
        "entry": clamp(base * 0.76 + 8),
    }
    values = np.array(list(roster.values()), dtype=float)
    synchrony = clamp(100 - float(np.std(values)) * 1.6)
    contagion = clamp(float(np.mean(values >= 65)) * 100)
    stabilizer = min(roster, key=roster.get)
    risk_carrier = max(roster, key=roster.get)
    return {
        "roster": roster,
        "synchrony": round(synchrony, 1),
        "contagion": round(contagion, 1),
        "stabilizer": stabilizer,
        "risk_carrier": risk_carrier,
    }


def build_adaptive_game_api_directive() -> dict:
    readiness = build_universal_readiness_score()
    directive = "maintain"
    if readiness["readiness"] < 35:
        directive = "protect_player_agency_soften_spike"
    elif readiness["readiness"] < 55:
        directive = "offer_micro_pause_or_lower_noise"
    elif readiness["readiness"] > 82:
        directive = "allow_challenge_increase"
    return {
        "type": "user_owned_readiness.directive",
        "consent": "player_owned_opt_in",
        "readiness": readiness,
        "directive": directive,
        "allowed_actions": [
            "adjust_audio_intensity",
            "suggest_micro_break",
            "delay_noncritical_prompt",
            "coach_overlay_only",
        ],
        "blocked_actions": [
            "hidden_difficulty_manipulation_without_consent",
            "store_raw_video",
        ],
    }


def build_before_after_proof_card() -> dict:
    series = list(st.session_state.session_series)
    report = build_post_match_report()
    command = coach_command_effectiveness()
    if not series:
        return {
            "headline": 'Proof card',
            "before": 0,
            "after": 0,
            "delta": 0,
            "cue": "n/a",
            "caption": 'scan Investor Demo Session',
        }
    tilts = [float(row.get("tilt", 0.0)) for row in series]
    before = max(tilts)
    after = tilts[-1]
    delta = max(before - after, 0.0)
    return {
        "headline": f"Tilt {before:.0f} -> {after:.0f} in {st.session_state.recovery_seconds or 'live'}s",
        "before": round(before, 1),
        "after": round(after, 1),
        "delta": round(delta, 1),
        "cue": command.get("best_command") or report.get("winning_command", "n/a"),
        "caption": f"{report.get('what_broke_state', 'state')} cleared    recovery {report.get('recovery_score', 0):.0f}%",
    }


def export_founder_deck_package() -> Optional[Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    package_dir = REPORTS_DIR / f"founder_deck_pack_{st.session_state.session_id}"
    package_dir.mkdir(parents=True, exist_ok=True)
    pitch_path = export_pitch_report()
    evidence = {
        "report": build_post_match_report(),
        "readiness": build_universal_readiness_score(),
        "proof_card": build_before_after_proof_card(),
        "causal": build_causal_intervention_graph(),
        "somatic_language": {
            key: value
            for key, value in build_somatic_language_engine().items()
            if key != "state_space"
        },
        "adaptive_game_api": build_adaptive_game_api_directive(),
        "team_synchrony": build_team_synchrony_model(),
        "twin_drift": detect_twin_drift(),
    }
    (package_dir / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    proof = build_before_after_proof_card()
    (package_dir / "before_after_proof_card.md").write_text(
        f"# {proof['headline']}\n\n{proof['caption']}\n\nCue: {proof['cue']}\n",
        encoding="utf-8",
    )
    if pitch_path and Path(pitch_path).exists():
        shutil.copy2(pitch_path, package_dir / Path(pitch_path).name)
    st.session_state.last_founder_deck_path = str(package_dir)
    return package_dir


def append_somatic_log(metrics: BiomechanicsMetrics) -> None:
    now = time.time()
    if now - st.session_state.last_log_write_at < 0.5:
        return

    st.session_state.last_log_write_at = now
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    row = {
        "session_id": st.session_state.session_id,
        "timestamp": metrics.timestamp,
        "data_mode": "landmarks_only" if st.session_state.landmark_only_logging else "landmarks_plus_runtime",
        "game_event": st.session_state.last_game_event,
        "tilt_score": round(metrics.tilt_score, 2),
        "nose_x": round(metrics.nose_x, 2),
        "nose_y": round(metrics.nose_y, 2),
        "left_shoulder_x": round(metrics.left_shoulder_x, 2),
        "left_shoulder_y": round(metrics.left_shoulder_y, 2),
        "right_shoulder_x": round(metrics.right_shoulder_x, 2),
        "right_shoulder_y": round(metrics.right_shoulder_y, 2),
        "nose_to_shoulders_ratio": round(metrics.nose_to_shoulders_ratio, 4),
        "shoulder_elevation_score": round(metrics.shoulder_elevation_score, 2),
        "shoulder_asymmetry_percent": round(metrics.shoulder_asymmetry_percent, 2),
        "shoulder_slope_degrees": round(metrics.shoulder_slope_degrees, 2),
        "jaw_clench_score": round(metrics.jaw_clench_score, 2),
        "jaw_blendshape_score": round(metrics.jaw_blendshape_score, 2),
        "voice_tension_score": round(float((st.session_state.get("voice_metrics") or {}).get("voice_tension_score", 0.0)), 2),
        "speech_rate_proxy": round(float((st.session_state.get("voice_metrics") or {}).get("speech_rate_proxy", 0.0)), 2),
        "input_chaos_score": round(float((st.session_state.get("input_metrics") or {}).get("input_chaos_score", 0.0)), 2),
        "recovery_score": round(float(st.session_state.recovery_score), 2),
        "emotion_primary": metrics.emotion_primary,
        "emotion_confidence": round(metrics.emotion_confidence, 2),
        "emotional_arousal_score": round(metrics.emotional_arousal_score, 2),
        "emotional_valence_score": round(metrics.emotional_valence_score, 2),
        "brow_tension_score": round(metrics.brow_tension_score, 2),
        "eye_focus_score": round(metrics.eye_focus_score, 2),
        "mouth_tension_score": round(metrics.mouth_tension_score, 2),
        "baseline_active": bool(st.session_state.baseline_profile),
        "triggers": "|".join(metrics.triggers),
    }

    file_exists = SOMATIC_LOG_PATH.exists()
    with SOMATIC_LOG_PATH.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    st.session_state.somatic_logs.appendleft(row)


def write_overlay_state(metrics: Optional[BiomechanicsMetrics]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "session_id": st.session_state.session_id,
        "running": bool(st.session_state.running),
        "tilt_score": round(float(st.session_state.tilt_score), 1),
        "coach_alert": st.session_state.coach_alert,
        "prediction": st.session_state.tilt_prediction,
        "fingerprint": st.session_state.somatic_fingerprint,
        "pipeline_status": st.session_state.pipeline_status,
        "face_status": st.session_state.face_status,
        "face_tracking_quality": round(float(st.session_state.face_tracking_quality), 1),
        "performance_profile": st.session_state.get("performance_profile") or {},
        "voice_metrics": st.session_state.get("voice_metrics") or {},
        "input_metrics": st.session_state.get("input_metrics") or {},
        "recovery": {
            "active": bool(st.session_state.recovery_active),
            "score": round(float(st.session_state.recovery_score), 1),
            "seconds": st.session_state.recovery_seconds,
        },
        "metrics": None,
    }
    if metrics:
        payload["metrics"] = {
            "shoulder_elevation_score": round(metrics.shoulder_elevation_score, 1),
            "shoulder_asymmetry_percent": round(metrics.shoulder_asymmetry_percent, 1),
            "jaw_clench_score": round(metrics.jaw_clench_score, 1),
            "emotion_primary": metrics.emotion_primary,
            "emotion_confidence": round(metrics.emotion_confidence, 1),
            "emotional_arousal_score": round(metrics.emotional_arousal_score, 1),
            "emotional_valence_score": round(metrics.emotional_valence_score, 1),
            "voice_tension_score": round(float((st.session_state.get("voice_metrics") or {}).get("voice_tension_score", 0.0)), 1),
            "input_chaos_score": round(float((st.session_state.get("input_metrics") or {}).get("input_chaos_score", 0.0)), 1),
            "recovery_score": round(float(st.session_state.recovery_score), 1),
            "triggers": metrics.triggers,
        }
    atomic_io.atomic_write_json(OVERLAY_STATE_PATH, payload)


def write_engine_heartbeat() -> None:
    if engine_status is None:
        return
    try:
        engine_status.write_engine_heartbeat(
            DATA_DIR,
            {
                "running": bool(st.session_state.get("running", False)),
                "session_id": st.session_state.get("session_id", ""),
                "pipeline_status": st.session_state.get("pipeline_status", ""),
                "face_status": st.session_state.get("face_status", ""),
                "frame_counter": int(st.session_state.get("frame_counter", 0)),
                "performance_profile": st.session_state.get("performance_profile") or {},
                "camera_index": int(st.session_state.get("camera_index", 0)),
            },
        )
    except Exception:
        pass


def read_overlay_state() -> Optional[dict]:
    if not OVERLAY_STATE_PATH.exists():
        return None
    try:
        return json.loads(OVERLAY_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def tilt_color(tilt_score: float) -> tuple[int, int, int]:
    if tilt_score >= 75:
        return (255, 43, 71)
    if tilt_score >= 45:
        return (255, 184, 77)
    return (48, 255, 145)


def draw_neon_pose_overlay(
    frame: np.ndarray,
    pose_landmarks: Optional[list[SimpleLandmark]],
    face_landmarks: Optional[list[SimpleLandmark]],
    tilt_score: float,
) -> np.ndarray:
    annotated = frame.copy()
    if not pose_landmarks:
        return annotated

    height, width = annotated.shape[:2]
    color = tilt_color(tilt_score)
    dim_color = tuple(max(int(channel * 0.28), 12) for channel in color)

    def point(landmarks: list[SimpleLandmark], index: int) -> tuple[int, int]:
        landmark = landmarks[index]
        return int(landmark.x * width), int(landmark.y * height)

    upper_body_connections = [
        (vision.PoseLandmark.NOSE.value, vision.PoseLandmark.LEFT_SHOULDER.value),
        (vision.PoseLandmark.NOSE.value, vision.PoseLandmark.RIGHT_SHOULDER.value),
        (vision.PoseLandmark.LEFT_SHOULDER.value, vision.PoseLandmark.RIGHT_SHOULDER.value),
        (vision.PoseLandmark.LEFT_SHOULDER.value, vision.PoseLandmark.LEFT_ELBOW.value),
        (vision.PoseLandmark.RIGHT_SHOULDER.value, vision.PoseLandmark.RIGHT_ELBOW.value),
    ]
    for start_index, end_index in upper_body_connections:
        start = pose_landmarks[start_index]
        end = pose_landmarks[end_index]
        if min(start.visibility, end.visibility, start.presence, end.presence) < 0.35:
            continue
        cv2.line(
            annotated,
            point(pose_landmarks, start_index),
            point(pose_landmarks, end_index),
            dim_color,
            7,
            cv2.LINE_AA,
        )
        cv2.line(
            annotated,
            point(pose_landmarks, start_index),
            point(pose_landmarks, end_index),
            color,
            2,
            cv2.LINE_AA,
        )

    hot_points = {
        vision.PoseLandmark.NOSE.value,
        vision.PoseLandmark.LEFT_SHOULDER.value,
        vision.PoseLandmark.RIGHT_SHOULDER.value,
        vision.PoseLandmark.LEFT_ELBOW.value,
        vision.PoseLandmark.RIGHT_ELBOW.value,
    }
    for index in hot_points:
        if index >= len(pose_landmarks):
            continue
        landmark = pose_landmarks[index]
        if min(landmark.visibility, landmark.presence) < 0.35:
            continue
        radius = 7 if index in {
            vision.PoseLandmark.NOSE.value,
            vision.PoseLandmark.LEFT_SHOULDER.value,
            vision.PoseLandmark.RIGHT_SHOULDER.value,
        } else 4
        cv2.circle(annotated, point(pose_landmarks, index), radius + 4, dim_color, -1, cv2.LINE_AA)
        cv2.circle(annotated, point(pose_landmarks, index), radius, color, -1, cv2.LINE_AA)

    if face_landmarks:
        # Legacy implementation note cleaned for English demo.
        # Legacy implementation note cleaned for English demo.
        if st.session_state.get("show_face_points_overlay", True):
            dot_color = (35, 135, 105) if tilt_score < 75 else (95, 30, 45)
            for face_point in face_landmarks:
                cv2.circle(
                    annotated,
                    (int(face_point.x * width), int(face_point.y * height)),
                    1,
                    dot_color,
                    -1,
                    cv2.LINE_AA,
                )
        mouth_indexes = [13, 14, 61, 291, 78, 308, 0, 17]
        eye_indexes = [33, 133, 159, 145, 263, 362, 386, 374]
        brow_indexes = [70, 105, 336, 300, 9, 10]
        jaw_color = (85, 220, 255) if tilt_score < 75 else (255, 43, 71)
        eye_color = (48, 255, 145) if tilt_score < 75 else (255, 184, 77)
        brow_color = (255, 184, 77) if tilt_score < 75 else (255, 43, 71)
        for index in mouth_indexes:
            if index < len(face_landmarks):
                cv2.circle(annotated, point(face_landmarks, index), 3, jaw_color, -1, cv2.LINE_AA)
        for index in eye_indexes:
            if index < len(face_landmarks):
                cv2.circle(annotated, point(face_landmarks, index), 3, eye_color, -1, cv2.LINE_AA)
        for index in brow_indexes:
            if index < len(face_landmarks):
                cv2.circle(annotated, point(face_landmarks, index), 3, brow_color, -1, cv2.LINE_AA)

    cv2.putText(
        annotated,
        f"TILT {tilt_score:05.1f}%",
        (28, 46),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.05,
        color,
        2,
        cv2.LINE_AA,
    )
    return annotated


def open_camera(
    camera_index: int,
    width: int = 640,
    height: int = 360,
    fps: int = 15,
) -> Optional[cv2.VideoCapture]:
    backends = []
    if os.name == "nt":
        backends.extend([cv2.CAP_DSHOW, cv2.CAP_MSMF])
    backends.append(0)

    for backend in backends:
        cap = cv2.VideoCapture(camera_index, backend) if backend else cv2.VideoCapture(camera_index)
        if not cap or not cap.isOpened():
            if cap is not None:
                cap.release()
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if hasattr(cv2, "VideoWriter_fourcc"):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        ok = False
        for _ in range(8):
            ok, _frame = cap.read()
            if ok:
                break
            time.sleep(0.05)
        if ok:
            return cap
        cap.release()
    return None


def selected_camera_settings() -> tuple[int, int, int]:
    resolution = st.session_state.get("camera_resolution", "640x360")
    width_text, height_text = resolution.split("x")
    width = int(width_text)
    height = int(height_text)
    fps = int(st.session_state.get("camera_fps", 15))

    if st.session_state.get("fast_mode", True):
        width = min(width, 640)
        height = min(height, 360)
        fps = min(fps, 15)

    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    # Legacy implementation note cleaned for English demo.
    st.session_state.effective_camera_width = width
    st.session_state.effective_camera_height = height
    st.session_state.effective_camera_fps = fps
    return width, height, fps


def close_camera() -> None:
    cap = st.session_state.get("camera")
    if cap is not None:
        cap.release()
        time.sleep(0.12)
    st.session_state.camera = None


def start_camera_session(status_prefix: str = "Running") -> bool:
    close_camera()
    width, height, fps = selected_camera_settings()
    st.session_state.camera = open_camera(
        st.session_state.camera_index,
        width,
        height,
        fps,
    )
    st.session_state.running = st.session_state.camera is not None
    if st.session_state.running:
        st.session_state.pipeline_status = f"{status_prefix}: camera {st.session_state.camera_index}, {width}x{height}@{fps}"
        return True
    st.session_state.pipeline_status = (
        f"Camera failed on index {st.session_state.camera_index}. "
        "Close Zoom/Discord/OBS/browser camera tabs or try camera index 1."
    )
    return False


def reset_runtime_state(keep_alert: bool = False) -> None:
    st.session_state.smoothed_pose_landmarks = None
    st.session_state.smoothed_face_landmarks = None
    st.session_state.last_face_landmarks = None
    st.session_state.last_face_blendshapes = {}
    st.session_state.frame_counter = 0
    st.session_state.last_frame_ts_ms = 0
    st.session_state.last_tilt_update_at = 0.0
    st.session_state.tilt_score = 0.0
    st.session_state.shoulder_elevated_seconds = 0.0
    st.session_state.asymmetry_seconds = 0.0
    st.session_state.jaw_clench_seconds = 0.0
    st.session_state.emotional_tension_seconds = 0.0
    st.session_state.recovery_active = False
    st.session_state.recovery_score = 0.0
    st.session_state.recovery_seconds = None
    st.session_state.current_metrics = None
    st.session_state.tilt_prediction = {
        "seconds": None,
        "confidence": 0.0,
        "slope_per_sec": 0.0,
        "label": "collecting signal",
    }
    st.session_state.somatic_fingerprint = None
    st.session_state.post_match_report = None
    if not keep_alert:
        st.session_state.coach_alert = ""


def render_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #05070a;
            --bg-2: #090d12;
            --panel: rgba(13, 18, 24, 0.94);
            --panel-2: rgba(8, 12, 17, 0.88);
            --panel-3: rgba(18, 25, 33, 0.86);
            --line: rgba(130, 255, 190, 0.18);
            --line-strong: rgba(85, 220, 255, 0.32);
            --text: #f4fbf6;
            --muted: #9cb7ad;
            --muted-2: #6f887f;
            --green: #30ff91;
            --amber: #ffb84d;
            --red: #ff2b47;
            --cyan: #55dcff;
            --violet: #9f8cff;
            --shadow: 0 22px 70px rgba(0, 0, 0, 0.38);
        }
        html {
            color-scheme: dark;
        }
        .stApp {
            background:
                linear-gradient(90deg, rgba(255, 255, 255, 0.035) 1px, transparent 1px),
                linear-gradient(180deg, rgba(255, 255, 255, 0.028) 1px, transparent 1px),
                linear-gradient(135deg, #05070a 0%, #080d12 48%, #05070a 100%);
            background-size: 56px 56px, 56px 56px, auto;
            color: var(--text);
        }
        [data-testid="stHeader"] {
            background: rgba(5, 7, 10, 0.82);
            backdrop-filter: blur(18px);
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }
        .block-container {
            max-width: 1480px;
            padding-top: 1.1rem;
            padding-bottom: 2.4rem;
        }
        h1, h2, h3, h4, p, label, span, div {
            letter-spacing: 0;
        }
        h2, h3 {
            color: var(--text) !important;
            font-weight: 780 !important;
        }
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stCaptionContainer"],
        .stCaptionContainer {
            color: var(--muted) !important;
        }
        .hero-shell {
            border: 1px solid rgba(130, 255, 190, 0.18);
            background:
                linear-gradient(135deg, rgba(48, 255, 145, 0.105), rgba(85, 220, 255, 0.045) 44%, rgba(255, 255, 255, 0.018)),
                rgba(9, 13, 18, 0.86);
            border-radius: 8px;
            padding: 1.05rem 1.15rem;
            margin-bottom: 1rem;
            box-shadow: var(--shadow);
            position: relative;
            overflow: hidden;
        }
        .hero-shell::after {
            content: "";
            position: absolute;
            inset: 0;
            pointer-events: none;
            background:
                linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.035), transparent);
            transform: translateX(-62%);
            animation: scanline 7s linear infinite;
        }
        @keyframes scanline {
            0% { transform: translateX(-70%); }
            100% { transform: translateX(70%); }
        }
        .hero-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            position: relative;
            z-index: 1;
        }
        .brand-lockup {
            display: flex;
            align-items: center;
            gap: 0.78rem;
        }
        .brand-mark {
            width: 42px;
            height: 42px;
            border-radius: 8px;
            display: grid;
            place-items: center;
            color: #06100b;
            font-weight: 900;
            background: linear-gradient(135deg, var(--green), var(--cyan));
            box-shadow: 0 0 28px rgba(48, 255, 145, 0.24);
        }
        .app-title {
            font-size: 2.05rem;
            font-weight: 880;
            letter-spacing: 0;
            color: var(--text);
            margin: 0;
            line-height: 1;
        }
        .app-subtitle {
            color: #c7dfd7;
            font-size: 0.93rem;
            margin: 0.28rem 0 0 0;
            max-width: 860px;
            line-height: 1.35;
        }
        .hero-badges {
            display: flex;
            gap: 0.45rem;
            flex-wrap: wrap;
            justify-content: flex-end;
        }
        .hero-badge {
            border: 1px solid rgba(255, 255, 255, 0.12);
            background: rgba(255, 255, 255, 0.055);
            color: #dfffea;
            border-radius: 999px;
            padding: 0.34rem 0.62rem;
            font-size: 0.72rem;
            font-weight: 780;
            white-space: nowrap;
        }
        .panel {
            border: 1px solid var(--line);
            background:
                linear-gradient(180deg, rgba(255, 255, 255, 0.035), transparent 42%),
                linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
            border-radius: 8px;
            padding: 1.05rem;
            box-shadow: var(--shadow);
            margin-bottom: 0.85rem;
        }
        .panel:hover {
            border-color: rgba(85, 220, 255, 0.28);
        }
        .guide-panel {
            border: 1px solid rgba(85, 220, 255, 0.24);
            background:
                linear-gradient(135deg, rgba(85, 220, 255, 0.10), rgba(48, 255, 145, 0.035)),
                rgba(10, 16, 23, 0.82);
            border-radius: 8px;
            padding: 0.9rem;
            margin-bottom: 0.9rem;
        }
        .guide-title {
            font-size: 0.9rem;
            font-weight: 800;
            color: #bfefff;
            margin-bottom: 0.45rem;
        }
        .guide-step {
            display: grid;
            grid-template-columns: 26px 1fr;
            gap: 0.55rem;
            align-items: start;
            color: #d7efe4;
            font-size: 0.84rem;
            line-height: 1.3;
            padding: 0.24rem 0;
        }
        .step-num {
            height: 22px;
            width: 22px;
            border: 1px solid rgba(85, 220, 255, 0.32);
            border-radius: 999px;
            color: #55dcff;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.72rem;
            font-weight: 800;
        }
        .model-badge {
            border: 1px solid rgba(48, 255, 145, 0.18);
            background: rgba(48, 255, 145, 0.075);
            border-radius: 6px;
            padding: 0.68rem;
            font-size: 0.8rem;
            color: #b7ffd5;
            margin-top: 0.45rem;
        }
        .cockpit-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.6rem;
            margin-bottom: 0.8rem;
        }
        .cockpit-card {
            border: 1px solid rgba(255, 255, 255, 0.08);
            background:
                linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.02)),
                rgba(255, 255, 255, 0.025);
            border-radius: 6px;
            padding: 0.75rem;
            min-height: 86px;
        }
        .cockpit-label {
            color: var(--muted);
            font-size: 0.68rem;
            text-transform: uppercase;
            letter-spacing: 0.07em;
            margin-bottom: 0.25rem;
        }
        .cockpit-value {
            color: var(--text);
            font-size: 1.28rem;
            font-weight: 820;
            line-height: 1.08;
        }
        .cockpit-note {
            color: #b7cfc5;
            font-size: 0.76rem;
            line-height: 1.25;
            margin-top: 0.35rem;
        }
        .storyline {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.45rem;
            margin-top: 0.75rem;
        }
        .story-node {
            border: 1px solid rgba(85, 220, 255, 0.16);
            background: rgba(85, 220, 255, 0.045);
            border-radius: 6px;
            padding: 0.55rem;
            color: #dff8ff;
            font-size: 0.74rem;
            line-height: 1.25;
        }
        .pill-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            margin-top: 0.5rem;
        }
        .signal-pill {
            border: 1px solid rgba(48, 255, 145, 0.18);
            background: rgba(48, 255, 145, 0.07);
            color: #b7ffd5;
            border-radius: 999px;
            padding: 0.22rem 0.5rem;
            font-size: 0.7rem;
            font-weight: 720;
        }
        .privacy-strip {
            border: 1px solid rgba(48, 255, 145, 0.22);
            background: rgba(48, 255, 145, 0.055);
            border-radius: 8px;
            padding: 0.75rem;
            color: #dfffea;
            font-size: 0.82rem;
            line-height: 1.35;
        }
        .metric-label {
            color: var(--muted);
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.2rem;
        }
        .metric-value {
            color: var(--text);
            font-size: 1.28rem;
            font-weight: 720;
            margin-bottom: 0.65rem;
        }
        .meter-shell {
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(255, 255, 255, 0.035);
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 0.8rem;
        }
        .tilt-meter {
            height: 28px;
            border-radius: 4px;
            overflow: hidden;
            background: rgba(255, 255, 255, 0.075);
            position: relative;
            border: 1px solid rgba(255, 255, 255, 0.06);
        }
        .tilt-fill {
            height: 100%;
            border-radius: 4px;
            box-shadow: 0 0 24px currentColor;
            transition: width 140ms linear, background 140ms linear;
        }
        .tilt-number {
            font-size: 3.2rem;
            font-weight: 820;
            line-height: 1;
            margin-top: 0.75rem;
            letter-spacing: 0;
        }
        .alert-box {
            border: 1px solid rgba(255, 43, 71, 0.42);
            background: rgba(255, 43, 71, 0.10);
            color: var(--red);
            border-radius: 8px;
            padding: 1rem;
            font-size: 2rem;
            line-height: 1.05;
            font-weight: 850;
            text-transform: uppercase;
            text-shadow: 0 0 18px rgba(255, 43, 71, 0.35);
        }
        .log-box {
            font-family: Consolas, "SFMono-Regular", monospace;
            font-size: 0.74rem;
            line-height: 1.35;
            color: #b7ffd5;
            white-space: pre-wrap;
            max-height: 245px;
            overflow: auto;
            border: 1px solid rgba(48, 255, 145, 0.12);
            background: rgba(0, 0, 0, 0.24);
            border-radius: 6px;
            padding: 0.8rem;
        }
        .baseline-ready {
            border: 1px solid rgba(48, 255, 145, 0.22);
            background: rgba(48, 255, 145, 0.08);
            border-radius: 6px;
            padding: 0.75rem;
            color: #b7ffd5;
            font-size: 0.88rem;
        }
        .event-row {
            font-family: Consolas, "SFMono-Regular", monospace;
            font-size: 0.78rem;
            color: #d7efe4;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            padding: 0.35rem 0;
        }
        .overlay-note {
            border: 1px solid rgba(85, 220, 255, 0.22);
            background: rgba(85, 220, 255, 0.07);
            border-radius: 6px;
            color: #bfefff;
            padding: 0.7rem;
            font-size: 0.86rem;
            margin-bottom: 0.8rem;
        }
        .mini-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.55rem;
        }
        .mini-card {
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(255, 255, 255, 0.035);
            border-radius: 6px;
            padding: 0.72rem;
        }
        .mini-value {
            font-size: 1.18rem;
            font-weight: 780;
            color: var(--text);
            line-height: 1.1;
        }
        .mini-label {
            font-size: 0.68rem;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.07em;
            margin-bottom: 0.2rem;
        }
        .platform-contract {
            font-family: Consolas, "SFMono-Regular", monospace;
            color: #bfefff;
            background: rgba(85, 220, 255, 0.06);
            border: 1px solid rgba(85, 220, 255, 0.18);
            border-radius: 6px;
            padding: 0.65rem;
            font-size: 0.72rem;
            white-space: pre-wrap;
            overflow: auto;
            max-height: 170px;
        }
        .team-row {
            display: grid;
            grid-template-columns: 1fr 60px;
            gap: 0.65rem;
            align-items: center;
            padding: 0.4rem 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }
        [data-testid="stImage"] img {
            border-radius: 8px;
            border: 1px solid rgba(85, 220, 255, 0.18);
            box-shadow: 0 26px 86px rgba(0, 0, 0, 0.42);
        }
        [data-testid="stExpander"] {
            border: 1px solid rgba(255, 255, 255, 0.09) !important;
            background: rgba(9, 13, 18, 0.82) !important;
            border-radius: 8px !important;
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.20);
            overflow: hidden;
        }
        [data-testid="stExpander"] summary {
            color: var(--text) !important;
            font-weight: 760 !important;
            background: rgba(255, 255, 255, 0.035) !important;
            border-radius: 8px !important;
        }
        [data-testid="stWidgetLabel"] label,
        [data-testid="stWidgetLabel"] p,
        [data-testid="stCheckbox"] label,
        [data-testid="stCheckbox"] p {
            color: #e9f8f1 !important;
            font-weight: 660 !important;
            font-size: 0.88rem !important;
        }
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stTextArea"] textarea,
        div[data-baseweb="input"] input {
            background: rgba(5, 8, 12, 0.96) !important;
            color: var(--text) !important;
            -webkit-text-fill-color: var(--text) !important;
            border: 1px solid rgba(85, 220, 255, 0.22) !important;
            border-radius: 6px !important;
            box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.02) !important;
        }
        [data-testid="stTextInput"] input::placeholder,
        [data-testid="stNumberInput"] input::placeholder {
            color: #6f887f !important;
            -webkit-text-fill-color: #6f887f !important;
        }
        [data-testid="stTextInput"] input:focus,
        [data-testid="stNumberInput"] input:focus,
        [data-testid="stTextArea"] textarea:focus {
            border-color: rgba(48, 255, 145, 0.62) !important;
            box-shadow: 0 0 0 3px rgba(48, 255, 145, 0.12) !important;
        }
        [data-baseweb="select"] > div {
            background: rgba(5, 8, 12, 0.96) !important;
            color: var(--text) !important;
            border: 1px solid rgba(85, 220, 255, 0.22) !important;
            border-radius: 6px !important;
        }
        [data-baseweb="select"] div,
        [data-baseweb="select"] span {
            color: var(--text) !important;
            -webkit-text-fill-color: var(--text) !important;
        }
        [data-baseweb="popover"] {
            background: #080c11 !important;
            border: 1px solid rgba(85, 220, 255, 0.24) !important;
            color: var(--text) !important;
        }
        [role="option"] {
            background: #080c11 !important;
            color: var(--text) !important;
        }
        [role="option"]:hover,
        [aria-selected="true"] {
            background: rgba(48, 255, 145, 0.14) !important;
            color: #dfffea !important;
        }
        [data-testid="stCheckbox"] [data-testid="stMarkdownContainer"] p {
            color: #e9f8f1 !important;
        }
        [data-baseweb="checkbox"] > div {
            background: rgba(5, 8, 12, 0.96) !important;
            border-color: rgba(85, 220, 255, 0.42) !important;
        }
        [data-baseweb="checkbox"][aria-checked="true"] > div,
        [data-baseweb="checkbox"] input:checked + div {
            background: var(--green) !important;
            border-color: var(--green) !important;
        }
        [data-testid="stSlider"] div[data-baseweb="slider"] div {
            color: var(--text) !important;
        }
        [data-testid="stSlider"] [role="slider"] {
            background: var(--green) !important;
            border: 2px solid #06100b !important;
            box-shadow: 0 0 0 4px rgba(48, 255, 145, 0.18) !important;
        }
        [data-testid="stSlider"] [data-testid="stTickBar"] {
            color: var(--muted) !important;
        }
        div.stButton > button {
            min-height: 2.58rem;
            border-radius: 6px !important;
            border: 1px solid rgba(48, 255, 145, 0.46) !important;
            background:
                linear-gradient(180deg, rgba(48, 255, 145, 0.26), rgba(48, 255, 145, 0.11)) !important;
            color: #effff7 !important;
            font-weight: 820 !important;
            box-shadow: 0 10px 28px rgba(48, 255, 145, 0.11) !important;
            text-shadow: none !important;
            transition: transform 120ms ease, border-color 120ms ease, box-shadow 120ms ease, background 120ms ease;
        }
        [data-testid="stButton"] button,
        button[data-testid="baseButton-secondary"],
        button[data-testid="baseButton-primary"] {
            border-radius: 6px !important;
            border: 1px solid rgba(48, 255, 145, 0.46) !important;
            background:
                linear-gradient(180deg, rgba(48, 255, 145, 0.26), rgba(48, 255, 145, 0.11)) !important;
            color: #effff7 !important;
            box-shadow: 0 10px 28px rgba(48, 255, 145, 0.11) !important;
        }
        div.stButton > button *,
        div.stButton > button p,
        div.stButton > button span,
        [data-testid="stButton"] button *,
        button[data-testid="baseButton-secondary"] *,
        button[data-testid="baseButton-primary"] * {
            color: #effff7 !important;
            -webkit-text-fill-color: #effff7 !important;
            font-weight: 820 !important;
        }
        div.stButton > button:hover {
            border-color: rgba(85, 220, 255, 0.72) !important;
            background:
                linear-gradient(180deg, rgba(85, 220, 255, 0.28), rgba(48, 255, 145, 0.13)) !important;
            color: #ffffff !important;
            transform: translateY(-1px);
            box-shadow: 0 14px 34px rgba(85, 220, 255, 0.14) !important;
        }
        [data-testid="stButton"] button:hover,
        button[data-testid="baseButton-secondary"]:hover,
        button[data-testid="baseButton-primary"]:hover {
            border-color: rgba(85, 220, 255, 0.72) !important;
            background:
                linear-gradient(180deg, rgba(85, 220, 255, 0.28), rgba(48, 255, 145, 0.13)) !important;
            color: #ffffff !important;
        }
        div.stButton > button:active {
            transform: translateY(0);
            background: rgba(48, 255, 145, 0.18) !important;
        }
        div.stButton > button:disabled,
        div.stButton > button[disabled] {
            background: rgba(255, 255, 255, 0.06) !important;
            border-color: rgba(255, 255, 255, 0.10) !important;
            color: rgba(244, 251, 246, 0.45) !important;
        }
        div.stButton > button:disabled *,
        div.stButton > button[disabled] * {
            color: rgba(244, 251, 246, 0.45) !important;
            -webkit-text-fill-color: rgba(244, 251, 246, 0.45) !important;
        }
        @media (max-width: 900px) {
            .hero-top {
                align-items: flex-start;
                flex-direction: column;
            }
            .hero-badges {
                justify-content: flex-start;
            }
            .cockpit-grid,
            .storyline {
                grid-template-columns: 1fr;
            }
            .tilt-number {
                font-size: 2.55rem;
            }
        }

        /* === Mission Control: top-of-cockpit "flight deck" =================
           A small, dense band that gives non-technical testers the only four
           numbers they need to read the room (tilt / readiness / camera /
           fps) plus a status chip and one big primary action below. The rest
           of the cockpit (sensitivity sliders, baseline, foundation model,
           demo integrations) keeps living below it untouched. */
        .mc-shell {
            background: linear-gradient(180deg, rgba(20, 28, 38, 0.94), rgba(8, 12, 17, 0.94));
            border: 1px solid rgba(85, 220, 255, 0.18);
            border-radius: 18px;
            padding: 18px 22px;
            margin: 4px 0 14px;
            box-shadow: 0 18px 60px rgba(0, 0, 0, 0.35);
        }
        .mc-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
        }
        .mc-card {
            background: rgba(255, 255, 255, 0.025);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 14px;
            padding: 14px 18px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .mc-card-primary {
            background: linear-gradient(180deg, rgba(48, 255, 145, 0.08), rgba(48, 255, 145, 0.02));
            border-color: rgba(48, 255, 145, 0.28);
        }
        .mc-card-primary.mc-band-medium {
            background: linear-gradient(180deg, rgba(255, 184, 77, 0.12), rgba(255, 184, 77, 0.02));
            border-color: rgba(255, 184, 77, 0.32);
        }
        .mc-card-primary.mc-band-high {
            background: linear-gradient(180deg, rgba(255, 43, 71, 0.14), rgba(255, 43, 71, 0.02));
            border-color: rgba(255, 43, 71, 0.4);
        }
        .mc-card-label {
            font-size: 11px;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: var(--muted);
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .mc-card-value {
            font-size: 38px;
            font-weight: 800;
            line-height: 1;
            letter-spacing: -1px;
        }
        .mc-card-suffix {
            font-size: 14px;
            color: var(--muted-2);
            font-weight: 500;
            margin-left: 4px;
        }
        .mc-card-band {
            font-size: 11px;
            letter-spacing: 0.14em;
            color: var(--muted);
        }
        .mc-card-primary.mc-band-low .mc-card-band { color: #30ff91; }
        .mc-card-primary.mc-band-medium .mc-card-band { color: #ffb84d; }
        .mc-card-primary.mc-band-high .mc-card-band { color: #ff2b47; }
        .mc-tip {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.08);
            color: var(--muted);
            font-size: 10px;
            cursor: help;
            margin-left: 4px;
        }
        .mc-bar-row {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-top: 14px;
            padding-top: 12px;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            color: var(--muted);
            font-size: 14px;
            flex-wrap: wrap;
        }
        .mc-status-chip {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11px;
            letter-spacing: 0.14em;
            font-weight: 700;
        }
        .mc-status-green   { background: rgba(48, 255, 145, 0.18); color: #30ff91; }
        .mc-status-building{ background: rgba(255, 184, 77, 0.16); color: #ffb84d; }
        .mc-status-tense   { background: rgba(255, 184, 77, 0.22); color: #ffb84d; }
        .mc-status-alert   { background: rgba(255, 43, 71, 0.22);  color: #ff2b47; }
        .mc-status-offline { background: rgba(255, 255, 255, 0.06); color: var(--muted); }
        .mc-status-text { color: var(--text); }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_tilt_meter(metrics: Optional[BiomechanicsMetrics]) -> None:
    tilt = float(st.session_state.tilt_score)
    color_hex = "#ff2b47" if tilt >= 75 else "#ffb84d" if tilt >= 45 else "#30ff91"
    st.markdown(
        f"""
        <div class="meter-shell">
            <div class="metric-label">Tilt indicator</div>
            <div class="tilt-meter">
                <div class="tilt-fill" style="width:{tilt:.1f}%; background:{color_hex}; color:{color_hex};"></div>
            </div>
            <div class="tilt-number" style="color:{color_hex};">{tilt:.0f}%</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if metrics:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">Shoulder elevation</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="metric-value">{metrics.shoulder_elevation_score:.1f}</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="metric-label">Shoulder asymmetry</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="metric-value">{metrics.shoulder_asymmetry_percent:.1f}% | {metrics.shoulder_slope_degrees:.1f} deg</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="metric-label">Jaw tension</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="metric-value">{metrics.jaw_clench_score:.1f}</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"Face blendshape score: {metrics.jaw_blendshape_score:.1f} | "
            f"jaw open ratio: {metrics.jaw_open_ratio if metrics.jaw_open_ratio is not None else 'n/a'}"
        )
        st.caption(
            f"Face tracking quality: {st.session_state.face_tracking_quality:.0f}%"
        )
        st.caption(
            f"Raised shoulders: {st.session_state.shoulder_elevated_seconds:.1f}s / 3.0s | "
            f"Asymmetry: {st.session_state.asymmetry_seconds:.1f}s"
        )
        st.markdown("</div>", unsafe_allow_html=True)

        render_emotion_panel(metrics)


def render_quick_guide() -> None:
    if not st.session_state.get("guided_ui", True):
        return
    st.markdown(
        """
        <div class="guide-panel">
            <div class="guide-title">How to read this screen</div>
            <div class="guide-step"><div class="step-num">1</div><div><b>Start scanning.</b> The camera looks for posture, shoulders, face, eyes, mouth and jaw.</div></div>
            <div class="guide-step"><div class="step-num">2</div><div><b>Calibrate neutral.</b> Ten seconds of calm posture creates a personal baseline.</div></div>
            <div class="guide-step"><div class="step-num">3</div><div><b>Watch Tilt + Emotion.</b> The system adds auto-labels for focus, frustration, fatigue, stress and control.</div></div>
            <div class="guide-step"><div class="step-num">4</div><div><b>After an alert, watch Recovery.</b> Recovery measures how quickly the body returns to baseline after a coach command.</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_mission_control() -> None:
    """The top "flight deck" of the cockpit: 4 numbers a tester actually
    cares about (tilt, readiness, coach status, FPS) plus one big primary
    action. Everything else stays available below - just no longer in the
    user's face. This is purely a presentation layer; it reads from
    session_state and never mutates it.
    """
    running = bool(st.session_state.get("running", False))
    tilt_score = float(st.session_state.get("tilt_score", 0.0) or 0.0)
    recovery_score = float(st.session_state.get("recovery_score", 0.0) or 0.0)
    face_quality = float(st.session_state.get("face_tracking_quality", 0.0) or 0.0)
    perf = st.session_state.get("performance_profile") or {}
    fps_value = float(perf.get("fps") or 0.0)
    coach_alert = (st.session_state.get("coach_alert") or "").strip()
    pipeline_status = (st.session_state.get("pipeline_status") or "").strip()

    if not running:
        status_chip = "OFFLINE"
        status_class = "mc-status-offline"
        status_hint = "Camera is not active. Press Start session so the coach can read body-state signals."
    elif coach_alert:
        status_chip = "ALERT"
        status_class = "mc-status-alert"
        status_hint = coach_alert
    elif tilt_score >= 60:
        status_chip = "TENSE"
        status_class = "mc-status-tense"
        status_hint = "Tilt is rising. Soften jaw, drop shoulders, exhale."
    elif tilt_score >= 35:
        status_chip = "BUILDING"
        status_class = "mc-status-building"
        status_hint = "Mild tension. Coach is watching; no command needed yet."
    else:
        status_chip = "GREEN"
        status_class = "mc-status-green"
        status_hint = pipeline_status or "Body is calm. Coach is waiting for a real trigger."

    band = "high" if tilt_score >= 70 else "medium" if tilt_score >= 40 else "low"
    readiness = scoring.readiness_score(
        tilt=tilt_score,
        jaw=float((st.session_state.get("metrics_snapshot") or {}).get("jaw_clench_score", 0.0)),
        shoulders=float((st.session_state.get("metrics_snapshot") or {}).get("shoulder_elevation_score", 0.0)),
        recovery=recovery_score,
    )

    st.markdown(
        f"""
        <div class="mc-shell">
          <div class="mc-row">
            <div class="mc-card mc-card-primary mc-band-{band}">
              <div class="mc-card-label">Tilt level
                <span class="mc-tip" title="0-40 calm | 40-70 rising | 70+ high risk. Computed from posture, jaw, face and recovery.">?</span>
              </div>
              <div class="mc-card-value">{int(round(tilt_score))}<span class="mc-card-suffix">/100</span></div>
              <div class="mc-card-band">{band.upper()}</div>
            </div>
            <div class="mc-card">
              <div class="mc-card-label">Readiness
                <span class="mc-tip" title="How ready you are to play right now. Uses tilt, jaw, shoulders and recovery.">?</span>
              </div>
              <div class="mc-card-value">{int(round(readiness))}<span class="mc-card-suffix">/100</span></div>
              <div class="mc-card-band">{'READY' if readiness >= 65 else 'WORKING' if readiness >= 40 else 'LOW'}</div>
            </div>
            <div class="mc-card">
              <div class="mc-card-label">Camera
                <span class="mc-tip" title="Camera signal quality. Below 55%, coach alerts are suppressed to avoid false commands.">?</span>
              </div>
              <div class="mc-card-value">{int(round(face_quality))}<span class="mc-card-suffix">%</span></div>
              <div class="mc-card-band">{'GOOD' if face_quality >= 65 else 'WEAK' if face_quality > 0 else 'NO FACE'}</div>
            </div>
            <div class="mc-card">
              <div class="mc-card-label">Engine FPS
                <span class="mc-tip" title="How many frames per second the cockpit is processing. 8+ is healthy for this MVP.">?</span>
              </div>
              <div class="mc-card-value">{fps_value:.1f}</div>
              <div class="mc-card-band">{'OK' if fps_value >= 8 else 'SLOW' if fps_value > 0 else 'IDLE'}</div>
            </div>
          </div>
          <div class="mc-bar-row">
            <div class="mc-status-chip {status_class}">{status_chip}</div>
            <div class="mc-status-text">{status_hint}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    primary_col, secondary_col, tertiary_col = st.columns([2, 1, 1])
    with primary_col:
        if not running:
            if st.button(
                "Start session",
                key="mc_start_btn",
                use_container_width=True,
                type="primary",
                help="Opens the camera and starts the live engine. One click and the coach begins reading body-state signals.",
            ):
                close_camera()
                reset_runtime_state()
                st.session_state.session_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                st.session_state.session_series.clear()
                st.session_state.game_events.clear()
                st.session_state.somatic_logs.clear()
                st.session_state.external_event_offset = 0
                st.session_state.creator_viewer_resets = 0
                start_camera_session("Pose stream active")
        else:
            if st.button(
                "Stop",
                key="mc_stop_btn",
                use_container_width=True,
                help="Stops the camera and keeps the session report on screen.",
            ):
                st.session_state.running = False
                close_camera()
                st.session_state.pipeline_status = "Scanning stopped"
    with secondary_col:
        if st.button(
            "Calibrate (10 sec)",
            key="mc_calibrate_btn",
            use_container_width=True,
            help="Captures your neutral posture. This strongly improves personal accuracy.",
        ):
            if not st.session_state.running:
                start_camera_session("Calibration camera active")
            if st.session_state.running:
                start_calibration()
            else:
                st.session_state.pipeline_status = "Camera unavailable for calibration"
    with tertiary_col:
        if st.button(
            "Reset tilt",
            key="mc_reset_btn",
            use_container_width=True,
            help="Clears accumulated Tilt and the current coach command.",
        ):
            reset_runtime_state()
            st.session_state.coach_alert = ""


def render_model_stack_panel() -> None:
    backend = research_emotion_backend_status()
    active_backend = st.session_state.get("emotion_backend", "")
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("What the AI is analyzing now")
    st.markdown(
        f"""
        <div class="model-badge">
            <b>Active realtime backend:</b> {html.escape(active_backend)}<br>
            <b>Face:</b> up to 478 Face Landmarker points + 52 blendshape coefficients<br>
            <b>Body:</b> 33 Pose Landmarker points<br>
            <b>Interpretation:</b> facial tension + biomechanics + voice + mouse/keyboard + recovery
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Why not only off-the-shelf emotion recognition: FER models often fail without a personal baseline. We collect interpretable body-state features and build our own model on top.")
    st.caption(backend["research_status"])
    st.markdown("</div>", unsafe_allow_html=True)


def render_llm_health_panel() -> None:
    health = cached_llm_health()
    connected = bool(health.get("connected"))
    status = str(health.get("status", "fallback"))
    color = "#35f29a" if connected else ("#ffb84d" if status in {"timeout", "fallback"} else "#ff4f58")
    message = html.escape(str(health.get("message", "")))
    model = html.escape(str(health.get("model", get_llm_model())))
    latency = health.get("latency_ms")
    latency_text = f"{latency} ms" if latency is not None else "n/a"
    fallback_text = "LLM responding" if connected else "Local fallback alert active"
    st.markdown(
        f"""
        <div class="panel">
            <h3>LLM health check</h3>
            <div class="model-badge">
                <b style="color:{color}">* {html.escape(status.upper())}</b><br>
                <b>Model:</b> {model}<br>
                <b>Latency:</b> {latency_text}<br>
                <b>Fallback:</b> {fallback_text}<br>
                <span>{message}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_performance_profile_panel() -> None:
    profile = st.session_state.get("performance_profile") or {}
    st.markdown(
        f"""
        <div class="panel">
            <h3>Performance profile</h3>
            <div class="cockpit-grid">
                <div class="cockpit-card"><div class="cockpit-label">FPS</div><div class="cockpit-value">{float(profile.get("fps", 0.0)):.1f}</div></div>
                <div class="cockpit-card"><div class="cockpit-label">Pose</div><div class="cockpit-value">{float(profile.get("pose_ms", 0.0)):.0f} ms</div></div>
                <div class="cockpit-card"><div class="cockpit-label">Face</div><div class="cockpit-value">{float(profile.get("face_ms", 0.0)):.0f} ms</div></div>
                <div class="cockpit-card"><div class="cockpit-label">Frame</div><div class="cockpit-value">{float(profile.get("frame_ms", 0.0)):.0f} ms</div></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_investor_thesis_panel() -> None:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("What we are building")
    st.caption("Not just an emotion detector. This is a user-owned layer for human performance state: body, face, voice, input behavior, recovery and personal baseline.")
    st.caption("Research cockpit: local camera analysis, derived signals only. No raw video is stored.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_product_module_matrix() -> None:
    modules = [
        ("Founder Pitch Mode", "ready"),
        ("Somatic Replay", "ready"),
        ("Coach Command Effectiveness", "ready"),
        ("Causal Intervention Graph", "new"),
        ("Personal Somatic Twin", "ready"),
        ("Somatic Twin Memory", "new"),
        ("Next Best Intervention", "new"),
        ("Somatic Language Engine", "new"),
        ("Latent State Map", "new"),
        ("Pre-Tilt Prediction", "ready"),
        ("Body Language API", "ready"),
        ("Recovery Protocol Generator", "ready"),
        ("Data Moat Counter", "ready"),
        ("Privacy / Consent Layer", "ready"),
        ("Streamer OBS Overlay", "ready"),
        ("Team Coach Dashboard", "ready"),
        ("Game Event Bridge", "ready"),
        ("Voice Stress Layer", "beta"),
        ("Pitch Report Export", "ready"),
        ("OpenFace / AU Verifier", "offline"),
    ]
    pills = "".join(
        f'<span class="signal-pill">{html.escape(name)}    {html.escape(status)}</span>'
        for name, status in modules
    )
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Somatic Intelligence OS")
    st.markdown(f'<div class="pill-row">{pills}</div>', unsafe_allow_html=True)
    st.caption("Pipeline: sensing -> interpretation -> coaching -> recovery -> dataset -> model")
    st.markdown("</div>", unsafe_allow_html=True)


def render_founder_pitch_mode() -> None:
    report = build_post_match_report()
    command = coach_command_effectiveness()
    protocol = generate_recovery_protocol(report)
    causal = build_causal_intervention_graph()
    signature = build_somatic_signature()
    next_best = recommend_next_best_intervention()
    readiness = build_universal_readiness_score()
    moat = build_data_moat_stats()
    modalities = moat["modalities"]
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Founder Pitch Mode")
    st.caption(': , , data moat')
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card">
                <div class="cockpit-label">Human state API</div>
                <div class="cockpit-value">{readiness['readiness']:.0f}</div>
                <div class="cockpit-note">Human Readiness    {html.escape(readiness['label'])}</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Recovery</div>
                <div class="cockpit-value">{report.get('recovery_score', 0):.0f}%</div>
                <div class="cockpit-note">{html.escape(report.get('recovery_note', 'n/a'))}</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Data moat</div>
                <div class="cockpit-value">{moat['auto_label_samples']} samples</div>
                <div class="cockpit-note">{len(modalities)} modalities active</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Best command</div>
                <div class="cockpit-value">{command.get('effectiveness', 0):.0f}%</div>
                <div class="cockpit-note">{html.escape(command.get('best_command', 'n/a'))}</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Personal twin</div>
                <div class="cockpit-value">{html.escape(signature.get('signature_hash', 'pending'))}</div>
                <div class="cockpit-note">{html.escape(signature.get('dominant_lock', 'n/a'))} dominant lock</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Protocol</div>
                <div class="cockpit-value">{html.escape(protocol['name'])}</div>
                <div class="cockpit-note">{html.escape(protocol['cue'])}</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Causal proof</div>
                <div class="cockpit-value">{causal.get('causal_confidence', 0):.0f}%</div>
                <div class="cockpit-note">{causal.get('tilt_prevented', 0):.0f} tilt prevented</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Next best cue</div>
                <div class="cockpit-value">{html.escape(next_best.get('cue', 'n/a'))}</div>
                <div class="cockpit-note">{html.escape(next_best.get('reason', 'n/a'))}</div>
            </div>
        </div>
        <div class="storyline">
            <div class="story-node"><b>1. Event</b><br>{html.escape(report.get('trigger_event', 'game event'))}</div>
            <div class="story-node"><b>2. Body</b><br>{html.escape(report.get('what_broke_state', 'somatic signal'))}</div>
            <div class="story-node"><b>3. Emotion</b><br>{html.escape(report.get('dominant_emotion', 'auto-label'))}</div>
            <div class="story-node"><b>4. Coach</b><br>{html.escape(command.get('best_command', protocol['cue']))}</div>
            <div class="story-node"><b>5. Recovery</b><br>{report.get('recovery_score', 0):.0f}% restored</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_data_moat_panel() -> None:
    moat = build_data_moat_stats()
    modality_pills = "".join(
        f'<span class="signal-pill">{html.escape(modality)}</span>'
        for modality in moat["modalities"]
    )
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Data Moat Counter")
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card">
                <div class="cockpit-label">Auto-label dataset</div>
                <div class="cockpit-value">{moat['auto_label_samples']}</div>
                <div class="cockpit-note">JSONL samples                   </div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Human-state API</div>
                <div class="cockpit-value">{moat['api_events']}</div>
                <div class="cockpit-note">stream events     OBS/game/coach</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Personal baselines</div>
                <div class="cockpit-value">{moat['profiles']}</div>
                <div class="cockpit-note">             somatic twins</div>
            </div>
        </div>
        <div class="pill-row">{modality_pills}</div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(f"API stream: {BODY_LANGUAGE_API_PATH}")
    st.markdown("</div>", unsafe_allow_html=True)


def render_universal_readiness_panel(metrics: Optional[BiomechanicsMetrics] = None) -> None:
    readiness = build_universal_readiness_score(metrics)
    color = "#30ff91" if readiness["readiness"] >= 76 else "#ffb84d" if readiness["readiness"] >= 52 else "#ff2b47"
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Readiness Profile")
    st.caption("Focus, stress, recovery, fatigue, and body control.")
    st.markdown(
        f"""
        <div class="meter-shell">
            <div class="metric-label">Readiness</div>
            <div class="tilt-meter">
                <div class="tilt-fill" style="width:{readiness['readiness']:.1f}%; background:{color}; color:{color};"></div>
            </div>
            <div class="tilt-number" style="color:{color};">{readiness['readiness']:.0f}</div>
            <div style="color:#8da69c;">{html.escape(readiness['label'])}</div>
        </div>
        <div class="cockpit-grid">
            <div class="cockpit-card"><div class="cockpit-label">Focus</div><div class="cockpit-value">{readiness['focus']:.0f}</div></div>
            <div class="cockpit-card"><div class="cockpit-label">Stress</div><div class="cockpit-value">{readiness['stress']:.0f}</div></div>
            <div class="cockpit-card"><div class="cockpit-label">Recovery</div><div class="cockpit-value">{readiness['recovery']:.0f}</div></div>
            <div class="cockpit-card"><div class="cockpit-label">Fatigue</div><div class="cockpit-value">{readiness['fatigue']:.0f}</div></div>
            <div class="cockpit-card"><div class="cockpit-label">Control</div><div class="cockpit-value">{readiness['control']:.0f}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_somatic_autopilot_panel() -> None:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Somatic Autopilot")
    st.caption("Suggested micro-intervention from current body-state signals.")
    cue = st.session_state.autopilot_cue or "Waiting for a clear cue"
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card">
                <div class="cockpit-label">Status</div>
                <div class="cockpit-value">{'ON' if st.session_state.autopilot_enabled else 'OFF'}</div>
                <div class="cockpit-note">{html.escape(st.session_state.autopilot_status)}</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Current cue</div>
                <div class="cockpit-value">{html.escape(cue)}</div>
                <div class="cockpit-note">short intervention suggestion</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_body_state_embeddings_panel(metrics: Optional[BiomechanicsMetrics] = None) -> None:
    stats = body_state_embedding_stats(metrics)
    similar_rows = "".join(
        f'<div class="event-row">{html.escape(item["timestamp"])}    similarity={item["similarity"]}%    tilt={item["tilt"]}    {html.escape(item["phrase"])}    {html.escape(item["emotion"])}</div>'
        for item in stats["similar"]
    )
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Body-State Memory")
    st.caption("Find similar derived body-state moments. No raw video is stored.")
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card">
                <div class="cockpit-label">Stored states</div>
                <div class="cockpit-value">{stats['count']}</div>
                <div class="cockpit-note">{html.escape(stats['model'])}</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Last phrase</div>
                <div class="cockpit-value">{html.escape(stats['last_phrase'])}</div>
                <div class="cockpit-note">most recent embedded body-state cue</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if similar_rows:
        st.markdown('<div class="metric-label">Similar moments</div>', unsafe_allow_html=True)
        st.markdown(similar_rows, unsafe_allow_html=True)
    else:
        st.caption("No similar moments yet. Run a live session to collect derived body-state memory.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_before_after_proof_card() -> None:
    proof = build_before_after_proof_card()
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader('Before / After')
    st.markdown(
        f"""
        <div class="meter-shell">
            <div class="metric-label">Pitch proof</div>
            <div class="tilt-number" style="color:#30ff91;">{html.escape(proof['headline'])}</div>
            <div style="color:#d7efe4;font-size:1rem;margin-top:.5rem;">{html.escape(proof['caption'])}</div>
            <div class="model-badge"><b>Worked cue:</b> {html.escape(proof['cue'])}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_twin_drift_panel() -> None:
    drift = detect_twin_drift()
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Twin Drift Detection")
    st.caption("Checks whether current body-state drifted from personal baseline.")
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card">
                <div class="cockpit-label">Drift score</div>
                <div class="cockpit-value">{drift.get('drift_score', 0):.0f}%</div>
                <div class="cockpit-note">{html.escape(drift.get('label', 'n/a'))}</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Reason</div>
                <div class="cockpit-value">{html.escape(drift.get('reason', 'n/a'))}</div>
                <div class="cockpit-note">{html.escape(drift.get('status', 'n/a'))}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_micro_training_panel() -> None:
    training = generate_micro_training_plan()
    steps = "".join(
        f'<div class="story-node"><b>{html.escape(step.split(" ", 1)[0])}</b><br>{html.escape(step.split(" ", 1)[1] if " " in step else step)}</div>'
        for step in training["blocks"]
    )
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Micro-Training Generator")
    st.caption("3-minute drill based on the dominant lock.")
    st.markdown(
        f"""
        <div class="model-badge"><b>{html.escape(training['title'])}</b><br>{html.escape(training['cue'])}</div>
        <div class="storyline">{steps}</div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_team_synchrony_panel() -> None:
    team = build_team_synchrony_model()
    roster_rows = "".join(
        f'<div class="team-row"><div>{html.escape(name)}</div><div style="color:{("#ff2b47" if value >= 70 else "#ffb84d" if value >= 45 else "#30ff91")};font-weight:800;">{value:.0f}%</div></div>'
        for name, value in team["roster"].items()
    )
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Team Synchrony")
    st.caption("Team-level pressure proxy from tilt and facial arousal.")
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card"><div class="cockpit-label">Synchrony</div><div class="cockpit-value">{team['synchrony']:.0f}%</div></div>
            <div class="cockpit-card"><div class="cockpit-label">Contagion</div><div class="cockpit-value">{team['contagion']:.0f}%</div></div>
            <div class="cockpit-card"><div class="cockpit-label">Stabilizer</div><div class="cockpit-value">{html.escape(team['stabilizer'])}</div></div>
        </div>
        {roster_rows}
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_somatic_search_panel() -> None:
    results = st.session_state.get("somatic_search_results", [])
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Somatic Search")
    st.caption("Search derived body-state moments by jaw, tilt, event or recovery.")
    if not results:
        st.caption("No matching moments yet.")
    else:
        for result in results:
            st.markdown(
                f'<div class="event-row">{html.escape(result["time"])}    score={result["score"]}    tilt={result["tilt"]}    {html.escape(result["phrase"])}    {html.escape(result["emotion"])}</div>',
                unsafe_allow_html=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)


def render_coach_copilot_panel() -> None:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Coach Copilot")
    st.caption("Ask about clutch prep, breakdown drivers, recovery and commands.")
    st.markdown(
        f'<div class="platform-contract">{html.escape(st.session_state.copilot_answer)}</div>',
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_adaptive_game_api_panel() -> None:
    directive = build_adaptive_game_api_directive()
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Adaptive NPC / Game API Demo")
    st.caption('User-owned API: readiness')
    st.markdown(
        f'<div class="platform-contract">{html.escape(json.dumps(directive, ensure_ascii=False, indent=2))}</div>',
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_somatic_consent_passport() -> None:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Somatic Consent Passport")
    st.markdown(
        f"""
        <div class="privacy-strip">
            <b>Stored:</b> landmarks, face blendshapes, posture metrics, voice/input proxies, timestamps, auto-label confidence, recovery.<br>
            <b>Never stored:</b> raw video, raw audio, frames, biometric identity documents.<br>
            <b>Local path:</b> {html.escape(str(DATA_DIR))}.<br>
            <b>API:</b> derived body-state events only, opt-in.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_somatic_twin_memory_panel() -> None:
    signature = build_somatic_signature()
    memory = load_somatic_twin_memory()
    next_best = recommend_next_best_intervention(memory)
    cue_count = len(memory.get("cue_memory", {}))
    driver_count = len(memory.get("driver_memory", {}))
    weights = signature.get("modality_weights", {})
    weight_pills = "".join(
        f'<span class="signal-pill">{html.escape(key)} {value:.0f}</span>'
        for key, value in weights.items()
    )
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Somatic Twin Memory")
    st.caption("Personal memory of cues, drivers and recovery outcomes.")
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card">
                <div class="cockpit-label">Signature hash</div>
                <div class="cockpit-value">{html.escape(signature.get('signature_hash', 'pending'))}</div>
                <div class="cockpit-note">privacy-preserving somatic fingerprint</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Dominant lock</div>
                <div class="cockpit-value">{html.escape(signature.get('dominant_lock', 'n/a'))}</div>
                <div class="cockpit-note">primary body-state pattern</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Resilience</div>
                <div class="cockpit-value">{signature.get('resilience_score', 0):.0f}%</div>
                <div class="cockpit-note">recovery strength from recent sessions</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Entry speed</div>
                <div class="cockpit-value">{signature.get('stress_entry_speed', 0):.2f}</div>
                <div class="cockpit-note">tilt build-up speed</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Cue memory</div>
                <div class="cockpit-value">{cue_count}</div>
                <div class="cockpit-note">{driver_count} body drivers learned</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Next best intervention</div>
                <div class="cockpit-value">{html.escape(next_best.get('cue', 'n/a'))}</div>
                <div class="cockpit-note">{next_best.get('expected_effectiveness', 0):.0f}% expected from {html.escape(next_best.get('source', 'n/a'))}</div>
            </div>
        </div>
        <div class="pill-row">{weight_pills}</div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(st.session_state.somatic_twin_status)
    st.caption(f"Twin memory: {somatic_twin_path()}")
    st.markdown("</div>", unsafe_allow_html=True)


def render_somatic_language_engine_panel() -> None:
    language = build_somatic_language_engine()
    token_pills = "".join(
        f'<span class="signal-pill">{html.escape(token)} {count}</span>'
        for token, count in language.get("token_counts", {}).items()
    )
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Somatic Language Engine")
    st.caption("Tokenized body-state sequence for search and prediction.")
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card">
                <div class="cockpit-label">Somatic alphabet</div>
                <div class="cockpit-value">{language.get('alphabet_size', 0)}</div>
                <div class="cockpit-note">body-language tokens</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Entropy</div>
                <div class="cockpit-value">{language.get('entropy', 0):.0f}%</div>
                <div class="cockpit-note">state variability</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Next token</div>
                <div class="cockpit-value">{html.escape(language.get('next_token', 'n/a'))}</div>
                <div class="cockpit-note">{language.get('next_confidence', 0):.0f}% forecast confidence</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Rare transition</div>
                <div class="cockpit-value">{language.get('rare_transition_score', 0):.0f}%</div>
                <div class="cockpit-note">unusual state transition</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Current phrase</div>
                <div class="cockpit-value">{html.escape(language.get('current_phrase', 'n/a'))}</div>
                <div class="cockpit-note">current token phrase</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Status</div>
                <div class="cockpit-value">{html.escape(language.get('status', 'n/a'))}</div>
                <div class="cockpit-note">body sequence model</div>
            </div>
        </div>
        <div class="platform-contract">{html.escape(language.get('sentence', 'WAIT_FOR_SIGNAL'))}</div>
        <div class="pill-row">{token_pills}</div>
        """,
        unsafe_allow_html=True,
    )
    state_space = language.get("state_space")
    if state_space is not None and len(state_space) > 2:
        st.caption('Latent State Map: 2D- multimodal features')
        st.scatter_chart(
            state_space,
            x="Somatic X",
            y="Somatic Y",
            color="Tilt",
            height=220,
            use_container_width=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def render_command_effectiveness_panel() -> None:
    command = coach_command_effectiveness()
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Coach Command Effectiveness")
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card">
                <div class="cockpit-label">Effectiveness</div>
                <div class="cockpit-value">{command.get('effectiveness', 0):.0f}%</div>
                <div class="cockpit-note">{html.escape(command.get('status', 'n/a'))}</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Tilt drop</div>
                <div class="cockpit-value">{command.get('tilt_drop', 0):.0f}</div>
                <div class="cockpit-note">tilt reduction after cue</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Winning cue</div>
                <div class="cockpit-value">{html.escape(command.get('best_command', 'n/a'))}</div>
                <div class="cockpit-note">best observed short command</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_causal_intervention_panel() -> None:
    causal = build_causal_intervention_graph()
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Causal Intervention Engine")
    st.caption("Counterfactual estimate after the coach command.")
    st.markdown(
        f"""
        <div class="cockpit-grid">
            <div class="cockpit-card">
                <div class="cockpit-label">Causal confidence</div>
                <div class="cockpit-value">{causal.get('causal_confidence', 0):.0f}%</div>
                <div class="cockpit-note">{html.escape(causal.get('status', 'n/a'))}</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">Tilt prevented</div>
                <div class="cockpit-value">{causal.get('tilt_prevented', 0):.0f}</div>
                <div class="cockpit-note">estimated avoided tilt</div>
            </div>
            <div class="cockpit-card">
                <div class="cockpit-label">No-coach peak</div>
                <div class="cockpit-value">{causal.get('counterfactual_peak', 0):.0f}%</div>
                <div class="cockpit-note">projected peak without intervention</div>
            </div>
        </div>
        <div class="storyline">
            <div class="story-node"><b>Event</b><br>{html.escape(causal.get('trigger_event', 'n/a'))}</div>
            <div class="story-node"><b>Body driver</b><br>{html.escape(causal.get('body_driver', 'n/a'))}</div>
            <div class="story-node"><b>Emotion</b><br>{html.escape(causal.get('emotion_driver', 'n/a'))}</div>
            <div class="story-node"><b>Coach cue</b><br>{html.escape(causal.get('coach_command', 'n/a'))}</div>
            <div class="story-node"><b>Outcome</b><br>{causal.get('actual_after', 0):.0f}% actual tilt</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    chart = causal.get("chart")
    if chart:
        st.line_chart(chart, height=190)
    st.caption('MVP counterfactual model, . : intervention')
    st.markdown("</div>", unsafe_allow_html=True)


def render_privacy_consent_panel() -> None:
    st.markdown(
        f"""
        <div class="privacy-strip">
            <b>Privacy-first layer:</b> camera analysis stays local; only derived landmarks, scores, timestamps, labels and recovery proof are saved.
                                       somatic profile. Gemini                                      coach API.
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_emotion_panel(metrics: BiomechanicsMetrics) -> None:
    emotion_color = (
        "#ff2b47"
        if metrics.emotional_arousal_score >= 70
        else "#ffb84d"
        if metrics.emotional_arousal_score >= 45
        else "#30ff91"
    )
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="metric-label">Emotion Engine v0</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div style="font-size:1.7rem;font-weight:900;color:{emotion_color};line-height:1.05;">
            {html.escape(metrics.emotion_primary.upper())}
        </div>
        <div style="color:#8da69c;margin-top:0.25rem;">
            confidence {metrics.emotion_confidence:.0f}%    arousal {metrics.emotional_arousal_score:.0f}%    valence {metrics.emotional_valence_score:.0f}%
        </div>
        """,
        unsafe_allow_html=True,
    )

    top_scores = sorted(
        metrics.emotion_scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:4]
    for label, score in top_scores:
        bar_color = "#ff2b47" if score >= 70 else "#ffb84d" if score >= 45 else "#30ff91"
        st.markdown(
            f"""
            <div style="margin-top:0.45rem;">
                <div style="display:flex;justify-content:space-between;font-size:0.78rem;color:#8da69c;">
                    <span>{html.escape(label)}</span><span>{score:.0f}%</span>
                </div>
                <div style="height:6px;background:#11231c;border:1px solid rgba(48,255,145,.16);">
                    <div style="height:100%;width:{score:.1f}%;background:{bar_color};"></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.caption(
            ':'
            f"      {metrics.brow_tension_score:.0f}    "
            f"     /      {metrics.eye_focus_score:.0f}    "
            f"    {metrics.mouth_tension_score:.0f}    "
            f"   -           {st.session_state.emotional_tension_seconds:.1f}s"
        )
    st.caption(
        ': , , ,'
        ','
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_multimodal_telemetry_panel() -> None:
    voice = st.session_state.get("voice_metrics") or {}
    input_metrics = st.session_state.get("input_metrics") or {}
    voice_tension = float(voice.get("voice_tension_score", 0.0))
    input_chaos = float(input_metrics.get("input_chaos_score", 0.0))
    recovery = float(st.session_state.recovery_score)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="metric-label"> </div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="mini-grid">
            <div class="mini-card">
                <div class="mini-label">               </div>
                <div class="mini-value">{voice_tension:.0f}%</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">             </div>
                <div class="mini-value">{float(voice.get('speech_rate_proxy', 0)):.0f}%</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">Input chaos</div>
                <div class="mini-value">{input_chaos:.0f}%</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">Recovery</div>
                <div class="mini-value">{recovery:.0f}%</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(voice.get("voice_status", 'voice'))
    st.caption(input_metrics.get("input_status", 'mouse/keyboard'))
    if st.session_state.recovery_seconds is not None:
        st.caption(f"Recovery seconds: {st.session_state.recovery_seconds}s")
    elif st.session_state.recovery_active:
        st.caption('Recovery window :')
    st.markdown("</div>", unsafe_allow_html=True)


def render_baseline_panel() -> None:
    progress = calibration_progress()
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Baseline Calibration")
    if st.session_state.is_calibrating:
        st.progress(progress, text="Collecting neutral baseline")
        st.caption(
            f"Samples: {len(st.session_state.calibration_samples)}    "
            f"{progress * 100:.0f}%"
        )
    elif st.session_state.baseline_profile:
        baseline = st.session_state.baseline_profile
        jaw_baseline = (
            f"{baseline['jaw_open_ratio']:.4f}"
            if baseline["jaw_open_ratio"] is not None
            else "n/a"
        )
        st.markdown(
            f"""
            <div class="baseline-ready">
                Neutral baseline saved<br>
                Nose-to-shoulders ratio: {baseline['nose_to_shoulders_ratio']:.3f}<br>
                Shoulder asymmetry: {baseline['shoulder_asymmetry_percent']:.1f}%<br>
                Jaw baseline: {jaw_baseline}
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.caption("Run calibration to compare tilt against your neutral posture.")

    relaxed = st.session_state.jaw_relaxed_baseline
    clenched = st.session_state.jaw_clenched_baseline
    if relaxed or clenched or st.session_state.jaw_calibration_phase:
        phase = st.session_state.jaw_calibration_phase or "idle"
        st.caption(
            f"Jaw drill: {phase}    "
            f"relaxed={'saved' if relaxed else 'missing'}    "
            f"clenched={'saved' if clenched else 'missing'}"
        )
    st.markdown("</div>", unsafe_allow_html=True)


def render_prediction_panel() -> None:
    prediction = st.session_state.tilt_prediction
    seconds = prediction.get("seconds")
    if seconds is None:
        main = prediction.get("label", "collecting signal")
    else:
        main = f"{seconds:.0f}s to spike"
    st.markdown(
        f"""
        <div class="panel">
            <div class="metric-label">Pre-Tilt Prediction</div>
            <div class="metric-value">{html.escape(str(main))}</div>
            <div class="mini-grid">
                <div class="mini-card">
                    <div class="mini-label">Confidence</div>
                    <div class="mini-value">{prediction.get('confidence', 0):.0f}%</div>
                </div>
                <div class="mini-card">
                    <div class="mini-label">Slope</div>
                    <div class="mini-value">{prediction.get('slope_per_sec', 0):.2f}/s</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_fingerprint_panel() -> None:
    fingerprint = build_somatic_fingerprint()
    st.markdown(
        f"""
        <div class="panel">
            <div class="metric-label">Somatic Fingerprint</div>
            <div class="metric-value">{html.escape(fingerprint['archetype'])}</div>
            <div class="mini-grid">
                <div class="mini-card">
                    <div class="mini-label">Readiness</div>
                    <div class="mini-value">{fingerprint['readiness']:.0f}%</div>
                </div>
                <div class="mini-card">
                    <div class="mini-label">Recovery speed</div>
                    <div class="mini-value">{html.escape(fingerprint['recovery_speed'])}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_alert() -> None:
    if st.session_state.coach_alert:
        safe_alert = html.escape(st.session_state.coach_alert)
        st.markdown(f'<div class="alert-box">{safe_alert}</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div class="panel"><div class="metric-label">AI Coach Alert</div>'
            '<div style="color:#8da69c;">No alert right now. Waiting for a clear high-confidence body-state pattern.</div></div>',
            unsafe_allow_html=True,
        )


def render_creator_tools() -> None:
    overlay_url = 'http://localhost:8501/?overlay=1'
    twitch_payload = {
        "type": "twitch_extension_panel",
        "session_id": st.session_state.session_id,
        "tilt_score": round(float(st.session_state.tilt_score), 1),
        "viewer_action": "send_reset",
    }
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader('Creator Growth')
    st.caption("OBS/Twitch-ready creator loop.")
    st.code(overlay_url, language="text")
    st.caption(f"Viewer !reset count: {st.session_state.creator_viewer_resets}")
    st.markdown(
        f'<div class="platform-contract">{html.escape(json.dumps(twitch_payload, ensure_ascii=False, indent=2))}</div>',
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_team_dashboard() -> None:
    current = float(st.session_state.tilt_score)
    seed = int(sum(ord(ch) for ch in st.session_state.session_id) % 23)
    roster = [
        ("Arseny", current),
        ("Duelist", clamp(current * 0.74 + 12 + seed % 7)),
        ("IGL", clamp(current * 0.56 + 22)),
        ("Support", clamp(current * 0.42 + 18)),
        ("Flex", clamp(current * 0.63 + 9)),
    ]
    team_avg = float(np.mean([player[1] for player in roster]))
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Team Tilt Dashboard")
    st.caption("B2B view: compare players, spot pressure, protect team readiness.")
    for name, tilt in roster:
        color = "#ff2b47" if tilt >= 75 else "#ffb84d" if tilt >= 45 else "#30ff91"
        st.markdown(
            f"""
            <div class="team-row">
                <div>{html.escape(name)}</div>
                <div style="color:{color}; font-weight:800;">{tilt:.0f}%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.caption(f"Team average tilt: {team_avg:.1f}%")
    st.markdown("</div>", unsafe_allow_html=True)


def render_party_mode() -> None:
    current = float(st.session_state.tilt_score)
    party = {
        "you": current,
        "duo": clamp(current * 0.70 + 8),
        "entry": clamp(current * 0.50 + 20),
        "anchor": clamp(current * 0.38 + 14),
    }
    calm_members = sum(1 for value in party.values() if value < 45)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Discord Party Mode")
    st.caption('B2C loop: recovery challenge')
    for name, tilt in party.items():
        st.progress(int(tilt), text=f"{name}: {tilt:.0f}%      ")
    st.caption(f"Squad challenge: {calm_members}/{len(party)} calm. Keep the team below tilt threshold.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_platform_contracts() -> None:
    contract = {
        "overwolf_event": {
            "event": "death",
            "source": "overwolf",
            "match_id": "demo-match-001",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        },
        "implicit_labels": {
            "death": "pressure_event",
            "clutch": "high_pressure",
            "toxic_chat": "social_stress",
            "loss_streak": 'fatigue',
        },
        "input_telemetry": {
            "key_rate_5s": "spam keys / panic actions",
            "click_rate_5s": "impulsive clicks",
            "mouse_speed_proxy": "chaotic aim/movement proxy",
        },
        "discord_activity_state": {
            "party_id": "squad-demo",
            "tilt_score": round(float(st.session_state.tilt_score), 1),
            "recovery_challenge": True,
        },
        "inbox_path": str(EXTERNAL_EVENTS_INBOX_PATH),
    }
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Platform Contracts")
    st.caption('Overwolf, Twitch, Discord event contract')
    st.markdown(
        f'<div class="platform-contract">{html.escape(json.dumps(contract, ensure_ascii=False, indent=2))}</div>',
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_foundation_model_lab() -> None:
    model_prediction = st.session_state.somatic_model_prediction or {}
    openface = check_openface_bridge() if st.session_state.openface_status == 'OpenFace' else {
        "status": st.session_state.openface_status,
        "ready": "ready" in st.session_state.openface_status.lower(),
    }
    dataset_size = 0
    if AFFECTIVE_DATASET_PATH.exists():
        try:
            dataset_size = sum(1 for _ in AFFECTIVE_DATASET_PATH.open("r", encoding="utf-8"))
        except OSError:
            dataset_size = 0

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Somatic Foundation Lab")
    st.caption(
        'Auto-label : , confidence evidence'
        'session report'
    )
    st.markdown(
        f"""
        <div class="mini-grid">
            <div class="mini-card">
                <div class="mini-label">Auto-label samples</div>
                <div class="mini-value">{dataset_size}</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">Proto label</div>
                <div class="mini-value">{html.escape(model_prediction.get('label', 'pending'))}</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">Model confidence</div>
                <div class="mini-value">{float(model_prediction.get('confidence', 0)):.0f}%</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">AU bridge</div>
                <div class="mini-value">{'ready' if openface.get('ready') else 'pending'}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(st.session_state.somatic_model_status)
    st.caption(st.session_state.openface_status)
    st.caption(f"Dataset path: {AFFECTIVE_DATASET_PATH}")
    st.caption(f"Model path: {SOMATIC_MODEL_PATH}")
    st.markdown("</div>", unsafe_allow_html=True)


def render_session_replay() -> None:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Somatic Replay")
    if st.session_state.session_series:
        chart_data = {
            "Tilt": [row["tilt"] for row in st.session_state.session_series],
            "Raw stress": [row["raw"] for row in st.session_state.session_series],
            "Arousal": [row.get("arousal", 0) for row in st.session_state.session_series],
            "Jaw": [row.get("jaw", 0) for row in st.session_state.session_series],
            "Input chaos": [row.get("input", 0) for row in st.session_state.session_series],
            "Recovery": [row.get("recovery", 0) for row in st.session_state.session_series],
        }
        st.line_chart(chart_data, height=180)
    else:
        st.caption("No replay data yet. Start a session or run Investor Demo Session.")

    if st.session_state.game_events:
        st.caption("Game event markers")
        for event in list(st.session_state.game_events)[:5]:
            safe_event = html.escape(
                f"{event['timestamp']}    {event['event']}        ={event['tilt_score']}%"
            )
            st.markdown(f'<div class="event-row">{safe_event}</div>', unsafe_allow_html=True)
    st.caption("Replay causal loop: event -> body signal -> coach command -> recovery proof")
    st.markdown("</div>", unsafe_allow_html=True)


def render_post_match_report() -> None:
    report = build_post_match_report()
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader(report["headline"])
    st.markdown(
        f"""
        <div class="mini-grid">
            <div class="mini-card">
                <div class="mini-label">Peak tilt</div>
                <div class="mini-value">{report['peak_tilt']:.0f}%</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">Average tilt</div>
                <div class="mini-value">{report['avg_tilt']:.0f}%</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">Events</div>
                <div class="mini-value">{report['event_count']}</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">Fingerprint</div>
                <div class="mini-value">{html.escape(report.get('fingerprint', {}).get('archetype', 'n/a'))}</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">Breakdown</div>
                <div class="mini-value">{html.escape(report.get('what_broke_state', 'n/a'))}</div>
            </div>
            <div class="mini-card">
                <div class="mini-label">Recovery</div>
                <div class="mini-value">{report.get('recovery_score', 0):.0f}%</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="platform-contract">
        driver: {html.escape(report.get('what_broke_state', 'n/a'))}
        peak moment: {html.escape(report.get('breakdown_moment', 'n/a'))}
        dominant state: {html.escape(report.get('dominant_emotion', 'n/a'))}
        trigger event: {html.escape(report.get('trigger_event', 'n/a'))}
        recovery cue: {html.escape(report.get('winning_command', 'n/a'))}
        recommendation: {html.escape(report.get('coach_recommendation', 'n/a'))}
        </div>
        """,
        unsafe_allow_html=True,
    )
    protocol = generate_recovery_protocol(report)
    st.markdown(
        f"""
        <div class="model-badge">
            <b>Recovery protocol:</b> {html.escape(protocol['name'])}<br>
            <b>Cue:</b> {html.escape(protocol['cue'])}<br>
            <b>Steps:</b> {html.escape(' / '.join(protocol['steps']))}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(report["insight"])
    st.caption(report["recovery_note"])
    st.markdown("</div>", unsafe_allow_html=True)


def render_somatic_logs() -> None:
    if not st.session_state.somatic_logs:
        log_text = '_ _ : true'
    else:
        log_text = "\n".join(
            json.dumps(row, ensure_ascii=False) for row in st.session_state.somatic_logs
        )

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.subheader("Derived Signal Log")
    st.caption(
        'Privacy mode: derived landmarks, timestamps and scores only'
        if st.session_state.landmark_only_logging
        else 'Runtime mode:'
    )
    st.caption(f"JSONL path: {SOMATIC_LOG_PATH}")
    st.markdown(f'<div class="log-box">{html.escape(log_text)}</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def render_overlay_from_state(
    video_placeholder,
    right_metrics_placeholder,
    alert_placeholder,
    logs_placeholder,
) -> None:
    state = read_overlay_state()
    if not state:
        video_placeholder.info(
            'Overlay'
        )
        return

    tilt = float(state.get("tilt_score", 0.0))
    color_hex = "#ff2b47" if tilt >= 75 else "#ffb84d" if tilt >= 45 else "#30ff91"
    video_placeholder.markdown(
        f"""
        <div class="panel" style="min-height:260px; display:flex; flex-direction:column; justify-content:center;">
            <div class="metric-label">OBS HUD                     </div>
            <div class="tilt-number" style="color:{color_hex};">{tilt:.0f}%</div>
            <div style="color:#8da69c;">{html.escape(state.get('updated_at', ''))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with right_metrics_placeholder.container():
        st.markdown(
            f"""
            <div class="meter-shell">
                <div class="metric-label">Tilt Risk</div>
                <div class="tilt-meter">
                    <div class="tilt-fill" style="width:{tilt:.1f}%; background:{color_hex}; color:{color_hex};"></div>
                </div>
                <div class="tilt-number" style="color:{color_hex};">{tilt:.0f}%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        prediction = state.get("prediction") or {}
        seconds = prediction.get("seconds")
        label = f"{seconds:.0f}s to spike" if seconds is not None else prediction.get("label", "collecting signal")
        metrics = state.get("metrics") or {}
        st.caption(f"Prediction: {label}")
        st.caption(f"Face quality: {state.get('face_tracking_quality', 0)}%")
        if metrics.get("emotion_primary"):
            st.caption(
                'Emotion: '
                f"{metrics.get('emotion_primary')}    "
                f"{metrics.get('emotion_confidence', 0)}%    "
                f"arousal {metrics.get('emotional_arousal_score', 0)}%"
            )
    with alert_placeholder.container():
        alert = state.get("coach_alert") or "No coach alert yet."
        st.markdown(f'<div class="alert-box">{html.escape(alert)}</div>', unsafe_allow_html=True)
    with logs_placeholder.container():
        st.markdown(
            f'<div class="platform-contract">{html.escape(json.dumps(metrics, ensure_ascii=False, indent=2))}</div>',
            unsafe_allow_html=True,
        )


def process_camera_frame(
    video_placeholder,
    right_metrics_placeholder,
    alert_placeholder,
    logs_placeholder,
) -> None:
    write_engine_heartbeat()
    poll_llm_future()
    ingest_external_game_events()
    if st.session_state.enable_voice_layer:
        voice_service = get_voice_telemetry()
        voice_service.start()
        st.session_state.voice_metrics = voice_service.snapshot()
    else:
        get_voice_telemetry().stop()
        st.session_state.voice_metrics = {}

    if st.session_state.enable_input_telemetry:
        input_service = get_input_telemetry()
        input_service.start()
        st.session_state.input_metrics = input_service.snapshot()
    else:
        get_input_telemetry().stop()
        st.session_state.input_metrics = {}

    if not st.session_state.running:
        if st.session_state.overlay_mode:
            render_overlay_from_state(
                video_placeholder,
                right_metrics_placeholder,
                alert_placeholder,
                logs_placeholder,
            )
            return
        video_placeholder.info("Camera is not active. Press Start session so the coach can read body-state signals.")
        return

    cap = st.session_state.camera
    if cap is None or not cap.isOpened():
        st.session_state.pipeline_status = "Camera failed"
        video_placeholder.error("Camera is unavailable. Check camera index or close another app using it.")
        return

    frame_profile_started = time.perf_counter()
    capture_started = time.perf_counter()
    ok, frame = cap.read()
    capture_ms = (time.perf_counter() - capture_started) * 1000
    if not ok:
        st.session_state.pipeline_status = "Frame read failed"
        video_placeholder.warning("Camera frame was not received. Restart the session or try another camera index.")
        return

    frame = cv2.flip(frame, 1)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))

    timestamp_ms = int(time.monotonic() * 1000)
    if timestamp_ms <= st.session_state.last_frame_ts_ms:
        timestamp_ms = st.session_state.last_frame_ts_ms + 1
    st.session_state.last_frame_ts_ms = timestamp_ms

    pose_started = time.perf_counter()
    pose_results = get_pose_detector().detect_for_video(mp_image, timestamp_ms)
    pose_ms = (time.perf_counter() - pose_started) * 1000
    pose_landmarks = None
    face_landmarks = None
    face_blendshapes = st.session_state.last_face_blendshapes or {}
    metrics = None
    face_ms = 0.0

    if pose_results.pose_landmarks:
        st.session_state.frame_counter += 1
        pose_landmarks = smooth_landmarks(
            pose_results.pose_landmarks[0],
            "smoothed_pose_landmarks",
            width,
            height,
            st.session_state.pose_smoothing_alpha,
            st.session_state.dead_zone_px,
        )

        face_detector = get_face_detector() if st.session_state.enable_face_mesh else None
        if face_detector is not None:
            should_run_face = (
                st.session_state.frame_counter
                % max(
                    int(st.session_state.get("face_every_n_frames", 3)),
                    3 if st.session_state.get("fast_mode", True) else 1,
                )
                == 0
            )
            if should_run_face:
                face_started = time.perf_counter()
                face_results = face_detector.detect_for_video(mp_image, timestamp_ms)
                face_ms = (time.perf_counter() - face_started) * 1000
            else:
                face_results = None

            if face_results and face_results.face_landmarks:
                face_landmarks = smooth_landmarks(
                    face_results.face_landmarks[0],
                    "smoothed_face_landmarks",
                    width,
                    height,
                    st.session_state.face_smoothing_alpha,
                    max(st.session_state.dead_zone_px * 0.45, 1.0),
                )
                face_blendshapes = extract_face_blendshapes(face_results)
                st.session_state.last_face_landmarks = face_landmarks
                st.session_state.last_face_blendshapes = face_blendshapes
                st.session_state.face_status = 'Face Mesh'
            else:
                face_landmarks = st.session_state.last_face_landmarks
                if face_landmarks:
                    st.session_state.face_status = (
                        f"Face Mesh    :      {st.session_state.frame_counter}"
                    )
                else:
                    st.session_state.smoothed_face_landmarks = None
                    st.session_state.face_status = 'Face Mesh:'
        else:
            st.session_state.face_status = 'Face Mesh'

        st.session_state.face_tracking_quality = estimate_face_tracking_quality(
            face_landmarks,
            face_blendshapes,
        )
        metrics = calculate_biomechanics(
            pose_landmarks=pose_landmarks,
            face_landmarks=face_landmarks,
            face_blendshapes=face_blendshapes,
            frame_width=width,
            frame_height=height,
            shoulder_raise_ratio_threshold=st.session_state.shoulder_raise_ratio_threshold,
            shoulder_raise_sensitivity=st.session_state.shoulder_raise_sensitivity,
            asymmetry_threshold_percent=st.session_state.asymmetry_threshold_percent,
            jaw_clench_ratio_threshold=st.session_state.jaw_clench_ratio_threshold,
            baseline_profile=(
                None if st.session_state.is_calibrating else st.session_state.baseline_profile
            ),
        )
        update_jaw_calibration(metrics)
        if st.session_state.is_calibrating:
            update_calibration(metrics)
            st.session_state.tilt_score = 0.0
            metrics.tilt_score = 0.0
        else:
            update_tilt_meter(metrics)
            update_recovery_score(metrics)
            append_somatic_log(metrics)
            append_affective_dataset_sample(metrics)
            append_body_language_api_event(metrics)
            append_body_state_embedding(metrics)
            append_session_series(metrics)
            update_tilt_prediction()
            build_somatic_fingerprint()
            st.session_state.somatic_model_prediction = predict_somatic_proto_state(metrics)
            maybe_run_somatic_autopilot(metrics)
            maybe_generate_coach_alert(metrics)
            poll_llm_future()
        st.session_state.current_metrics = metrics
        write_overlay_state(metrics)
        if not st.session_state.is_calibrating and st.session_state.pipeline_status.startswith("Calibrating"):
            st.session_state.pipeline_status = "Running"
        elif not st.session_state.is_calibrating and st.session_state.pipeline_status != 'baseline calibration':
            st.session_state.pipeline_status = "Running"
    else:
        reset_runtime_state(keep_alert=True)
        st.session_state.pipeline_status = "No pose detected"
        st.session_state.face_tracking_quality = 0.0
        write_overlay_state(None)

    annotated = draw_neon_pose_overlay(
        rgb,
        pose_landmarks,
        face_landmarks,
        st.session_state.tilt_score,
    )
    video_placeholder.image(annotated, channels="RGB", use_container_width=True)

    with right_metrics_placeholder.container():
        if st.session_state.overlay_mode:
            st.markdown(
                '<div class="overlay-note">OBS Overlay Mode: HUD .</div>',
                unsafe_allow_html=True,
            )
        render_tilt_meter(metrics)
        render_universal_readiness_panel(metrics)
        render_multimodal_telemetry_panel()
        render_prediction_panel()
        render_baseline_panel()
        if not st.session_state.overlay_mode:
            render_fingerprint_panel()
        st.caption(f"{st.session_state.pipeline_status}    {st.session_state.face_status}")

    with alert_placeholder.container():
        render_alert()

    with logs_placeholder.container():
        if not st.session_state.overlay_mode:
            ui_mode = st.session_state.get("ui_mode", "Pitch cockpit")
            if ui_mode == "Pitch cockpit":
                render_founder_pitch_mode()
                render_somatic_autopilot_panel()
                render_before_after_proof_card()
                render_causal_intervention_panel()
                render_post_match_report()
                render_somatic_consent_passport()
            elif ui_mode == "Operator cockpit":
                render_somatic_autopilot_panel()
                render_body_state_embeddings_panel(metrics)
                render_somatic_twin_memory_panel()
                render_somatic_language_engine_panel()
                render_twin_drift_panel()
                render_micro_training_panel()
                render_somatic_search_panel()
                render_coach_copilot_panel()
                render_team_synchrony_panel()
                render_adaptive_game_api_panel()
                render_command_effectiveness_panel()
                render_data_moat_panel()
            else:
                render_founder_pitch_mode()
                render_data_moat_panel()
                render_somatic_twin_memory_panel()
                render_body_state_embeddings_panel(metrics)
                render_somatic_language_engine_panel()
                render_twin_drift_panel()
                render_command_effectiveness_panel()
                render_causal_intervention_panel()
                render_session_replay()
                render_post_match_report()
                render_creator_tools()
                render_team_dashboard()
                render_party_mode()
                render_platform_contracts()
                render_foundation_model_lab()
                render_somatic_logs()

    frame_ms = (time.perf_counter() - frame_profile_started) * 1000
    now = time.perf_counter()
    last_frame_at = float(st.session_state.get("last_perf_frame_at", 0.0) or 0.0)
    fps = 1.0 / max(now - last_frame_at, 1e-6) if last_frame_at else 0.0
    st.session_state.last_perf_frame_at = now
    st.session_state.performance_profile = {
        "fps": round(fps, 1),
        "capture_ms": round(capture_ms, 1),
        "pose_ms": round(pose_ms, 1),
        "face_ms": round(face_ms, 1),
        "frame_ms": round(frame_ms, 1),
    }


def main() -> None:
    st.set_page_config(
        page_title="Kinaesthetic AI Tilt Meter",
        page_icon="KA",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    init_session_state()
    if str(st.query_params.get("overlay", "")).lower() in {"1", "true", "yes"}:
        st.session_state.overlay_mode = True
    render_styles()

    st.markdown(
        """English documentation cleaned for investor demo.""",
        unsafe_allow_html=True,
    )

    render_mission_control()

    layout_ratio = [2.25, 0.75] if st.session_state.overlay_mode else [1.55, 1.0]
    left, right = st.columns(layout_ratio, gap="large")

    with right:
        render_quick_guide()
        st.selectbox(
            "Workspace mode",
            ["Pitch cockpit", "Operator cockpit", "Research lab"],
            key="ui_mode",
            format_func=lambda mode: {
                "Pitch cockpit": "Pitch cockpit - demo ready",
                "Operator cockpit": "Operator cockpit - live monitoring",
                "Research lab": "Research lab - diagnostics",
            }.get(mode, mode),
            help="Choose how much operational detail to show.",
        )
        render_model_stack_panel()
        render_llm_health_panel()
        render_performance_profile_panel()
        if st.session_state.get("ui_mode") == "Research lab":
            render_product_module_matrix()
        else:
            with st.expander('Demo & investor tools', expanded=False):
                render_investor_thesis_panel()

        with st.expander("Settings", expanded=False):
            st.checkbox(
                "Guided UI",
                key="guided_ui",
                help="Show simplified player guidance and hide noisy research panels.",
            )
            st.selectbox(
                "Emotion backend",
                [
                    'Realtime MediaPipe: 478 + 52 blendshapes',
                    "OpenFace AU verifier: offline Action Units",
                    "DeepFace/AffectNet research: offline validation",
                ],
                key="emotion_backend",
                help="Realtime MediaPipe runs locally. OpenFace/DeepFace are offline validation options.",
            )
            st.checkbox(
                "OBS overlay mode",
                key="overlay_mode",
                help="HUD for OBS Browser Source: http://localhost:8501/?overlay=1",
            )
            st.checkbox(
                "Landmark-only logging",
                key="landmark_only_logging",
                help="Store derived landmarks, scores, confidence and recovery only. No raw video.",
            )
            st.checkbox(
                'Auto-label Somatic Foundation Model',
                key="auto_dataset_enabled",
                help="Collect derived body-state signals, labels and recovery outcomes.",
            )
            st.checkbox(
                "Voice proxy",
                key="enable_voice_layer",
                help="Use voice tension proxy if available; no raw audio is stored.",
            )
            st.checkbox(
                "Mouse/keyboard telemetry",
                key="enable_input_telemetry",
                help="Use mouse/keyboard proxy events, not raw gameplay footage.",
            )
            st.checkbox(
                "Somatic Autopilot",
                key="autopilot_enabled",
                help="Suggest short jaw/shoulder reset drills.",
            )
            st.checkbox(
                "Cloud LLM coach wording",
                key="use_llm_alerts",
                help="Use Groq/Gemini for non-critical coach wording. Local alerts still work.",
            )
            st.selectbox(
                "Coach style",
                list(COACH_MODE_PROMPTS.keys()),
                key="coach_mode",
                help="Choose the tone for short coaching commands.",
            )
            st.text_input(
                'Groq API',
                type="password",
                key="typed_groq_api_key",
                value=os.getenv("GROQ_API_KEY", ""),
                placeholder='gsk... ( provider)',
            )
            st.text_input(
                'Gemini API',
                type="password",
                key="typed_gemini_api_key",
                value=os.getenv("GEMINI_API_KEY", ""),
                placeholder='AIza... ( , Groq )',
            )
            st.text_input(
                "OpenAI-compatible Base URL (optional)",
                key="typed_base_url",
                value=os.getenv("OPENAI_BASE_URL", ""),
                placeholder='Gemini',
            )
            st.text_input(
                "LLM model",
                key="typed_model",
                value=os.getenv("GROQ_MODEL", os.getenv("GEMINI_MODEL", "llama-3.1-8b-instant")),
                placeholder="llama-3.1-8b-instant",
            )
            st.number_input(
                "LLM timeout, sec",
                min_value=3,
                max_value=60,
                value=8,
                step=1,
                key="llm_timeout_seconds",
            )

        with st.expander("Camera & CV", expanded=False):
            st.number_input(
                "Camera index",
                min_value=0,
                max_value=5,
                value=0,
                key="camera_index",
                help="Usually 0. Try 1 or 2 if another camera is connected.",
            )
            st.checkbox(
                'Face Landmarker',
                value=True,
                key="enable_face_mesh",
                help="478 face points and 52 blendshape coefficients for expression tension.",
            )
            st.checkbox(
                "Show face points overlay",
                key="show_face_points_overlay",
                help="Draw face landmarks on the local preview.",
            )
            st.checkbox(
                "Fast mode",
                key="fast_mode",
                help="Use lower resolution and lighter tracking for better FPS.",
            )
            st.selectbox(
                "Camera resolution",
                ["640x360", "960x540", "1280x720"],
                key="camera_resolution",
                help="640x360 is best for realtime; 960x540 is sharper.",
            )
            st.slider(
                'FPS',
                min_value=10,
                max_value=30,
                value=15,
                step=1,
                key="camera_fps",
                help="Higher FPS costs more CPU.",
            )
            st.slider(
                'Face Mesh: N',
                min_value=1,
                max_value=8,
                value=3,
                step=1,
                key="face_every_n_frames",
                help="1 = every frame. 3-5 = lighter CPU load.",
            )
            st.slider(
                "Shoulder raise ratio threshold",
                min_value=0.38,
                max_value=0.86,
                value=0.58,
                step=0.01,
                key="shoulder_raise_ratio_threshold",
                help="Calibrate neutral posture first for best results.",
            )
            st.slider(
                "Shoulder raise sensitivity",
                min_value=0.06,
                max_value=0.32,
                value=0.18,
                step=0.01,
                key="shoulder_raise_sensitivity",
                help="Lower values are more sensitive.",
            )
            st.slider(
                "Asymmetry threshold, %",
                min_value=2.0,
                max_value=18.0,
                value=5.0,
                step=0.5,
                key="asymmetry_threshold_percent",
                help="Lower values detect smaller shoulder imbalance.",
            )
            st.slider(
                "Jaw clench ratio threshold",
                min_value=0.006,
                max_value=0.045,
                value=0.018,
                step=0.001,
                key="jaw_clench_ratio_threshold",
                help="Use relaxed/clenched jaw calibration for a personal threshold.",
            )
            st.slider(
                "Pose smoothing alpha",
                min_value=0.05,
                max_value=0.60,
                value=0.18,
                step=0.01,
                key="pose_smoothing_alpha",
                help="Lower is smoother; higher is more responsive.",
            )
            st.slider(
                "Face smoothing alpha",
                min_value=0.05,
                max_value=0.60,
                value=0.22,
                step=0.01,
                key="face_smoothing_alpha",
                help="Lower is smoother; higher is more responsive.",
            )
            st.slider(
                "Dead zone, px",
                min_value=0.0,
                max_value=12.0,
                value=4.0,
                step=0.5,
                key="dead_zone_px",
                help="Ignore tiny jitter under this pixel threshold.",
            )
            st.slider(
                "LLM alert threshold",
                min_value=50,
                max_value=95,
                value=75,
                step=1,
                key="alert_threshold",
                help="Tilt threshold for cloud coach wording.",
            )
            st.number_input(
                "LLM cooldown, sec",
                min_value=10,
                max_value=240,
                value=60,
                step=10,
                key="llm_cooldown_seconds",
                help="Minimum time between cloud LLM coach calls.",
            )

        controls = st.columns(2)
        with controls[0]:
            if st.button(
                "Start session",
                use_container_width=True,
                help="Start camera, pose, face and emotion analysis.",
            ):
                close_camera()
                reset_runtime_state()
                st.session_state.session_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                st.session_state.session_series.clear()
                st.session_state.game_events.clear()
                st.session_state.somatic_logs.clear()
                st.session_state.external_event_offset = 0
                st.session_state.creator_viewer_resets = 0
                start_camera_session("Running")
        with controls[1]:
            if st.button(
                "Stop session",
                use_container_width=True,
                help="Stop camera and pause analysis.",
            ):
                st.session_state.running = False
                close_camera()
                st.session_state.pipeline_status = "Stopped"

        if st.button(
            "Reset tilt",
            use_container_width=True,
            help="Clear accumulated tilt/frustration state.",
        ):
            reset_runtime_state()
            st.session_state.coach_alert = ""

        if st.button(
            "Calibrate (10 sec)",
            use_container_width=True,
            help="Collect neutral posture, shoulders and jaw baseline.",
        ):
            if not st.session_state.running:
                start_camera_session("Calibration camera active")
            if st.session_state.running:
                start_calibration()
            else:
                st.session_state.pipeline_status = "Camera failed. Start session first or check camera index."

        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.subheader("Jaw calibration")
        jaw_cols = st.columns(2)
        with jaw_cols[0]:
            if st.button(
                'relaxed',
                use_container_width=True,
                help="Capture relaxed jaw as the neutral example.",
            ):
                if not st.session_state.running:
                    start_camera_session("Jaw calibration camera active")
                if st.session_state.running:
                    start_jaw_calibration_phase("relaxed")
        with jaw_cols[1]:
            if st.button(
                'clenched',
                use_container_width=True,
                help="Capture a short clenched-jaw example.",
            ):
                if not st.session_state.running:
                    start_camera_session("Jaw calibration camera active")
                if st.session_state.running:
                    start_jaw_calibration_phase("clenched")
        st.caption("Save relaxed and clenched jaw examples to improve jaw tension detection.")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.subheader('Copilot')
        st.text_input(
            "Somatic Search",
            key="somatic_search_query",
            placeholder="jaw lock after death / recovery faster than 15 sec / input spam",
            help='body-language',
        )
        if st.button("Search somatic moments", use_container_width=True, help='replay'):
            st.session_state.somatic_search_results = search_somatic_moments(st.session_state.somatic_search_query)
        st.text_input(
            "Coach Copilot question",
            key="copilot_question",
            placeholder='clutch',
            help='copilot cloud LLM',
        )
        if st.button("Ask Coach Copilot", use_container_width=True, help="Ask about current body-state patterns and recovery."):
            st.session_state.copilot_answer = answer_coach_copilot(st.session_state.copilot_question)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.subheader("Game event markers")
        event_cols = st.columns(2)
        with event_cols[0]:
            if st.button("Death", use_container_width=True, help="Mark a death/round loss moment."):
                record_game_event("death")
            if st.button("Loss streak", use_container_width=True, help="Mark a repeated loss sequence."):
                record_game_event("loss_streak")
        with event_cols[1]:
            if st.button("Clutch", use_container_width=True, help="Mark a clutch/high-pressure moment."):
                record_game_event("clutch")
            if st.button("Toxic chat", use_container_width=True, help="Mark a toxic chat or comms event."):
                record_game_event("toxic_chat")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.subheader("Somatic Foundation Model")
        st.text_input(
            'ID',
            key="user_profile_id",
            placeholder="arseny_main / player_001 / streamer_demo",
            help='baseline calibration',
        )
        st.text_input(
            'OpenFace FeatureExtraction.exe',
            key="openface_executable",
            placeholder=r"C:\Users\user\Desktop\MeirX\OpenFace\FeatureExtraction.exe",
            help='research-grade Action Units. MVP',
        )
        foundation_cols = st.columns(2)
        with foundation_cols[0]:
            if st.button("Check OpenFace AU", use_container_width=True, help="Check the optional OpenFace Action Units bridge."):
                check_openface_bridge()
        with foundation_cols[1]:
            if st.button("Train Proto Model", use_container_width=True, help="Train the nearest-centroid auto-label model."):
                train_somatic_proto_model()
        profile_cols = st.columns(2)
        with profile_cols[0]:
            if st.button("Save profile", use_container_width=True, help="Save current baseline and calibration profile."):
                save_personal_profile()
        with profile_cols[1]:
            if st.button("Load profile", use_container_width=True, help="Load saved baseline and calibration profile."):
                load_personal_profile()
        if st.button("Update Somatic Twin Memory", use_container_width=True, help="Save signature, body driver and recovery outcome."):
            update_somatic_twin_memory()
        st.caption(st.session_state.somatic_model_status)
        st.caption(st.session_state.openface_status)
        st.caption(st.session_state.profile_status)
        st.caption(st.session_state.somatic_twin_status)
        st.markdown("</div>", unsafe_allow_html=True)

        with st.expander('Demo & investor tools', expanded=False):
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.subheader("Demo integrations")
            if st.button("Simulate viewer reset", use_container_width=True, help="Adds a viewer-triggered reset cue for demo purposes."):
                st.session_state.creator_viewer_resets += 1
                st.session_state.coach_alert = "CHAT RESET: DROP SHOULDERS. EXHALE."
            if st.button("Build Investor Demo Session", use_container_width=True, help="Creates a scripted death -> tilt -> coach -> recovery session."):
                generate_investor_demo_session()
            if st.button("Export Session Report", use_container_width=True, help="Creates a Markdown report with breakdown, recovery and data moat."):
                export_pitch_report()
            if st.button("Export Founder Deck Pack", use_container_width=True, help="Exports investor evidence JSON files."):
                export_founder_deck_package()
            if st.button("Write Overwolf Demo Event", use_container_width=True, help="Writes a derived JSONL game event without raw video."):
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                payload = {
                    "event": "death",
                    "source": "overwolf",
                    "match_id": "demo-match-001",
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                }
                with EXTERNAL_EVENTS_INBOX_PATH.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(payload, ensure_ascii=False) + "\n")
                st.session_state.pipeline_status = "Overwolf demo event written"
            if st.session_state.last_exported_report_path:
                st.caption(f"Last pitch report: {st.session_state.last_exported_report_path}")
            if st.session_state.last_founder_deck_path:
                st.caption(f"Founder deck pack: {st.session_state.last_founder_deck_path}")
            st.markdown("</div>", unsafe_allow_html=True)

        right_metrics_placeholder = st.empty()
        alert_placeholder = st.empty()
        logs_placeholder = st.empty()

    with left:
        video_placeholder = st.empty()

    def camera_tick():
        process_camera_frame(
            video_placeholder,
            right_metrics_placeholder,
            alert_placeholder,
            logs_placeholder,
        )

    if hasattr(st, "fragment"):
        st.fragment(run_every="300ms")(camera_tick)()
    else:
        camera_tick()


if __name__ == "__main__":
    main()

