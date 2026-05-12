from __future__ import annotations

from pathlib import Path
from urllib.request import urlretrieve

from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)
FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)


def ensure_model(model_path: Path, model_url: str) -> None:
    if model_path.exists():
        return
    model_path.parent.mkdir(parents=True, exist_ok=True)
    urlretrieve(model_url, model_path)


def create_pose_detector(model_path: Path):
    ensure_model(model_path, POSE_MODEL_URL)
    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.62,
        min_pose_presence_confidence=0.62,
        min_tracking_confidence=0.62,
        output_segmentation_masks=False,
    )
    return vision.PoseLandmarker.create_from_options(options)


def create_face_detector(model_path: Path):
    ensure_model(model_path, FACE_MODEL_URL)
    options = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.55,
        min_face_presence_confidence=0.55,
        min_tracking_confidence=0.55,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=False,
    )
    return vision.FaceLandmarker.create_from_options(options)
