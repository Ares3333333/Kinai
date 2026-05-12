export const CV_CONFIG = {
  tasksVersion: "0.10.21",
  poseModelUrl: "/models/pose_landmarker_lite.task",
  faceModelUrl: "/models/face_landmarker.task",
  tiltSensitivityScaleDefault: 0.6,
  live: {
    tickMs: 220,
    signalPushMs: 1100,
    heartbeatMs: 5000,
    minTickMs: 220,
    maxTickMs: 480,
    retuneEveryTicks: 24,
  },
  profileScale: {
    low: 0.78,
    normal: 1.0,
    high: 1.2,
  },
  hysteresis: {
    jaw: { up: 70, down: 60, midUp: 40, midDown: 34 },
    shoulders: { up: 70, down: 60, midUp: 40, midDown: 34 },
    posture: { up: 70, down: 58, midUp: 45, midDown: 36 },
  },
  hardening: {
    videoFreezeTicks: 8,
    maxTiltStepPerTick: 16,
    confidenceEmaAlpha: 0.35,
  },
  minSignalConfidenceForNumbers: 0.55,
};

export const TASKS_SOURCES = [
  {
    name: "local_cache",
    bundle: "/vendor/mediapipe/vision_bundle.mjs",
    wasm: "/vendor/mediapipe/wasm",
  },
  {
    name: "cdn_fallback",
    bundle: `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${CV_CONFIG.tasksVersion}/vision_bundle.mjs`,
    wasm: `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${CV_CONFIG.tasksVersion}/wasm`,
  },
];
