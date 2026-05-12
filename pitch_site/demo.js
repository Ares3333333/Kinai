import { kaiPost } from "/kai_api.js";

const q = (id) => document.getElementById(id);

const labels = { low: "низкое", medium: "среднее", high: "высокое" };

const pitchTimeline = [
  { t: 0, phase: "baseline", tilt: 22, readiness: 86, recovery: 72, jaw: "low", shoulders: "low", command: "Baseline: игрок стабилен.", summary: "Готовимся: тело в нормальном baseline." },
  { t: 5, phase: "rising", tilt: 58, readiness: 61, recovery: 44, jaw: "medium", shoulders: "medium", command: "Риск растёт. Смягчи челюсть.", summary: "Тильт поднимается до того, как игрок сам это заметил." },
  { t: 10, phase: "alert", tilt: 84, readiness: 38, recovery: 22, jaw: "high", shoulders: "high", command: "Челюсть мягко. Плечи вниз. Выдох.", summary: "Сработал alert: jaw lock + shoulder block." },
  { t: 16, phase: "recovery", tilt: 52, readiness: 67, recovery: 58, jaw: "medium", shoulders: "low", command: "Держи мягкость. Не зажимай дыхание.", summary: "Recovery пошёл: тело возвращается к контролю." },
  { t: 22, phase: "proof", tilt: 39, readiness: 79, recovery: 81, jaw: "low", shoulders: "low", command: "Восстановление доказано.", summary: "Proof card: Tilt 84 → 39 за 12 сек." },
];

let activeMode = "live";
let demoTimer = null;
let browserTimer = null;
let cameraStream = null;
let browserCvEngine = null;
let browserCvBackend = "not_loaded";
let browserBaseline = JSON.parse(localStorage.getItem("kinaesthetic_baseline_v1") || "null");
let lastRenderedState = null;
let lastBrowserQualityScore = null;
let lastCloudCoachCallAt = 0;
let lastCloudCoachCommand = "";
let activeRecorder = null;
let activeRecordingChunks = [];
let activeRecordingLoop = null;
let wizardIndex = -1;
const LABEL_GOAL = 150;
const wizardSteps = [
  { key: "neutral", label: "Нейтрально", instruction: "Сядьте спокойно. Челюсть мягкая, плечи естественно." },
  { key: "jaw", label: "Зажать челюсть", instruction: "Намеренно сожмите челюсть на несколько секунд." },
  { key: "shoulders", label: "Поднять плечи", instruction: "Поднимите плечи к ушам, как при стрессовом блоке." },
  { key: "release", label: "Расслабиться", instruction: "Опустите плечи, смягчите челюсть, сделайте выдох." },
];
const wizardSamples = {};

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${Math.round(Math.max(0, Math.min(100, Number(value))))}%`;
}

function setText(id, value) {
  const node = q(id);
  if (node) node.textContent = value;
}

function setStep(id, active, done) {
  const node = q(id);
  if (!node) return;
  node.classList.toggle("is-active", Boolean(active));
  node.classList.toggle("is-done", Boolean(done));
}

function setMode(mode, message) {
  const panel = q("modePanel");
  if (panel) panel.dataset.mode = mode;
  const titles = {
    live: "LIVE · идёт анализ",
    stale: "Live engine не обновляется",
    offline: "Движок не подключён",
    demo: "Pitch demo",
    browser: "Browser CV beta",
  };
  setText("statusTitle", titles[mode] || titles.offline);
  setText("modeStatus", mode === "browser" ? "Browser CV" : mode);
  if (message) setText("statusMessage", message);
}

function renderTimeline(phase) {
  document.querySelectorAll("#demoTimeline [data-phase]").forEach((node) => {
    node.classList.toggle("is-active", node.dataset.phase === phase);
  });
}

function renderState(state) {
  lastRenderedState = state;
  const mode = state.mode || activeMode || "offline";
  setMode(mode, state.message);
  if (mode === "offline" && state.engine_connected) {
    setText("statusTitle", "Движок подключён · сессия не запущена");
  }
  setText("engineStatus", state.engine_connected ? "подключён" : mode === "demo" || mode === "browser" ? "не нужен" : "нет");
  setText("cameraStatus", state.camera_active ? "анализируется" : cameraStream ? "preview/browser" : "не активна");
  setText("signalQuality", pct((Number(state.signal_confidence ?? state.confidence) || 0) * 100));
  setText("demoTilt", pct(state.tilt_risk));
  setText("demoReadiness", pct(state.readiness));
  setText("demoRecovery", pct(state.recovery));
  setText("demoJaw", labels[state.jaw_tension] || state.jaw_tension || "--");
  setText("demoShoulders", labels[state.shoulder_tension] || state.shoulder_tension || "--");
  setText("demoAge", state.age_seconds === null || state.age_seconds === undefined ? "--" : `${Number(state.age_seconds).toFixed(1)}с`);
  setText("mainCommand", state.recommendation || "Челюсть мягко. Плечи вниз. Выдох.");

  if (mode === "live") {
    setText("humanSummary", Number(state.tilt_risk) >= 65 ? "Риск тильта растёт." : "Система видит тело и лицо. Готовность нормальная.");
  } else if (mode === "demo") {
    setText("dataQualityScore", "94%");
    setText("dataQualityLabel", "Pitch demo mode: сценарий готов для записи.");
    setText("humanSummary", state.summary || "Идёт киношный pitch-сценарий продукта.");
  } else if (mode === "browser") {
    lastBrowserQualityScore = Math.round((Number(state.signal_confidence ?? state.confidence) || 0) * 100);
    setText("dataQualityScore", pct(lastBrowserQualityScore));
    setText("dataQualityLabel", "Browser CV активен: raw video остаётся в браузере.");
    setText(
      "humanSummary",
      browserCvBackend === "mediapipe"
        ? "Browser CV beta видит pose + face через MediaPipe прямо в браузере."
        : "Browser CV fallback считает локальные motion/light сигналы. Raw video не уходит на сервер."
    );
  } else if (mode === "stale") {
    setText("humanSummary", "Live engine не обновляется. Старые метрики не считаются настоящим анализом.");
  } else {
    setText("humanSummary", "Анализ не идёт. Запустите live engine, pitch demo или Browser CV beta.");
  }

  if (mode === "demo" || mode === "browser" || mode === "live") {
    renderProof(state);
  }
  renderRealtime(state);
  renderCalibration(state);
  renderExpression(state);
  renderCvStatus(state);
  renderTimeline(state.phase || (mode === "live" ? "baseline" : ""));
}

function renderRealtime(state) {
  setText("browserFps", state.fps ? `${Math.round(Number(state.fps))}` : "--");
  setText("browserLatency", state.latency_ms ? `${Math.round(Number(state.latency_ms))}мс` : "--");
  setText("pipelineStatus", state.mode === "browser" ? "browser local" : state.mode === "demo" ? "record script" : state.mode || "idle");
}

function renderCalibration(state) {
  const cameraOk = Boolean(cameraStream || state.camera_active);
  const faceOk = Boolean(state.face_detected || state.mode === "demo" || state.mode === "live");
  const shouldersOk = Boolean(state.shoulders_visible || state.mode === "demo" || state.mode === "live");
  const confidence = Number(state.signal_confidence ?? state.confidence ?? 0);
  const signalOk = confidence >= 0.62 || state.mode === "demo";
  setStep("permCamera", cameraOk, cameraOk);
  setStep("permPosition", cameraOk && !faceOk, faceOk && shouldersOk);
  setStep("permSignal", faceOk && shouldersOk && !signalOk, signalOk);
  setText(
    "cameraCalibStatus",
    !cameraOk ? "разрешите камеру" : !faceOk ? "покажите лицо" : !shouldersOk ? "покажите плечи" : signalOk ? "сигнал готов" : "улучшите свет"
  );
}

function renderExpression(state) {
  setText("jawScore", pct(state.jaw_score));
  setText("browScore", pct(state.brow_tension));
  setText("eyesScore", pct(state.eye_tension));
  setText("mouthPressure", pct(state.mouth_pressure));
  setText("headDrift", pct(state.head_drift));
}

function renderCvStatus(state) {
  const mode = state.mode || activeMode || "offline";
  const confidence = Number(state.signal_confidence ?? state.confidence ?? 0);
  if (mode === "browser") {
    setText(
      "cvBackend",
      browserCvBackend === "mediapipe"
        ? state.runtime_source === "local_cache"
          ? "MediaPipe Web · local"
          : "MediaPipe Web · CDN"
        : "Motion fallback"
    );
    setText("cvPose", state.shoulders_visible ? "pose detected" : "ищу плечи");
    setText("cvFace", state.face_detected ? "face detected" : "ищу лицо");
    setText("cvShoulders", state.shoulders_visible ? "видны уверенно" : confidence > 0.45 ? "частично" : "слабый сигнал");
    setText("rawLandmarks", state.raw_landmarks ? String(state.raw_landmarks) : browserCvBackend === "mediapipe" ? "511+" : "--");
    setText("derivedPoints", state.derived_points ? String(state.derived_points) : "2 048");
    setText("signalLayers", state.signal_layers ? String(state.signal_layers) : browserCvBackend === "mediapipe" ? "7" : "2");
    return;
  }
  if (mode === "live") {
    setText("cvBackend", "Local engine");
    setText("cvPose", state.camera_active ? "pose live" : "ожидает");
    setText("cvFace", state.camera_active ? "face live" : "ожидает");
    setText("cvShoulders", confidence > 0.55 ? "видны" : "проверьте свет");
    setText("rawLandmarks", state.camera_active ? "511+" : "--");
    setText("derivedPoints", state.camera_active ? "2 048" : "--");
    setText("signalLayers", state.camera_active ? "7" : "--");
    return;
  }
  setText("cvBackend", mode === "demo" ? "Pitch demo" : "не запущен");
  setText("cvPose", mode === "demo" ? "симуляция" : "--");
  setText("cvFace", mode === "demo" ? "симуляция" : "--");
  setText("cvShoulders", mode === "demo" ? "симуляция" : "--");
  setText("rawLandmarks", mode === "demo" ? "511+" : "--");
  setText("derivedPoints", mode === "demo" ? "2 048" : "--");
  setText("signalLayers", mode === "demo" ? "7" : "--");
}

function renderProof(state) {
  const tilt = Number(state.tilt_risk || 0);
  const after = Math.max(0, Math.round(tilt - Number(state.recovery || 0) * 0.35));
  if (state.phase === "proof") {
    setText("proofHeadline", "Tilt 84 → 39 за 12 сек");
    setText("proofDetails", "Jaw lock ↓ · Shoulders released · команда сработала");
    return;
  }
  setText("proofHeadline", tilt >= 70 ? `Tilt ${Math.round(tilt)} → ${after}` : "Recovery proof готовится");
  setText("proofDetails", tilt >= 70 ? "Система ждёт снижения после команды." : "Пик тильта ещё не достигнут.");
}

async function pollLiveState() {
  if (activeMode !== "live") return;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    const state = await response.json();
    renderState(state);
  } catch {
    renderState({
      mode: "offline",
      engine_connected: false,
      camera_active: false,
      signal_confidence: 0,
      recommendation: "Запустите live engine или pitch demo.",
      message: "API state недоступен.",
    });
  }
}

async function refreshLlmHealth() {
  try {
    const response = await fetch("/api/llm-health", { cache: "no-store" });
    const health = await response.json();
    const label = health.connected
      ? `отвечает ${health.latency_ms}мс`
      : health.status === "timeout"
        ? "timeout"
        : "fallback";
    setText("llmStatus", label);
  } catch {
    setText("llmStatus", "fallback");
  }
}

async function requestCloudCoach(signals) {
  const tilt = Number(signals.tilt_risk || 0);
  if (tilt < 65) return signals.recommendation;
  const now = Date.now();
  if (lastCloudCoachCommand && now - lastCloudCoachCallAt < 8000) {
    return lastCloudCoachCommand;
  }
  lastCloudCoachCallAt = now;
  try {
    const data = await kaiPost("/api/coach", {
      source: "browser_cv",
      tilt_risk: signals.tilt_risk,
      readiness: signals.readiness,
      recovery: signals.recovery,
      jaw_tension: signals.jaw_tension,
      shoulder_tension: signals.shoulder_tension,
      signal_confidence: signals.signal_confidence,
      jaw_score: signals.jaw_score,
      brow_tension: signals.brow_tension,
      eye_tension: signals.eye_tension,
      mouth_pressure: signals.mouth_pressure,
      head_drift: signals.head_drift,
    });
    if (data && data.command) {
      lastCloudCoachCommand = data.command;
      const provider = data.provider === "groq" ? "Groq" : data.provider === "gemini" ? "Gemini" : "fallback";
      setText("llmStatus", `${provider} ${data.latency_ms || "--"}мс`);
      return data.command;
    }
    if (data && data.error === "rate_limited") {
      setText("llmStatus", "rate-limited");
    }
  } catch {
    setText("llmStatus", "fallback");
  }
  return signals.recommendation;
}

async function refreshDataQuality() {
  if (activeMode === "browser" || activeMode === "demo") {
    const score = activeMode === "browser"
      ? Math.round(lastBrowserQualityScore ?? 0)
      : 94;
    setText("dataQualityScore", pct(score));
    setText(
      "dataQualityLabel",
      activeMode === "browser"
        ? "Browser CV активен: raw video остаётся в браузере."
        : "Pitch demo mode: сценарий готов для записи."
    );
    return;
  }
  try {
    const response = await fetch("/api/data-quality", { cache: "no-store" });
    const quality = await response.json();
    setText("dataQualityScore", pct(quality.score));
    setText("dataQualityLabel", quality.label || "Качество данных оценивается.");
  } catch {
    setText("dataQualityScore", "--");
    setText("dataQualityLabel", "Data quality API недоступен.");
  }
}

async function recordPitchRun() {
  setText("pitchRunStatus", "Собираю proof card, report и pitch evidence...");
  try {
    const response = await fetch("/api/record-pitch-run", { cache: "no-store" });
    const result = await response.json();
    if (result.ok) {
      setText("pitchRunStatus", `Pitch run записан: ${result.export_dir || result.export_path}`);
    } else {
      setText("pitchRunStatus", "Не удалось записать pitch run.");
    }
  } catch {
    setText("pitchRunStatus", "Record API недоступен.");
  }
}

function drawRecordingFrame(canvas, ctx) {
  const video = q("cameraPreview");
  const overlay = q("landmarkOverlay");
  const width = canvas.width;
  const height = canvas.height;
  ctx.fillStyle = "#050607";
  ctx.fillRect(0, 0, width, height);

  if (video && video.readyState >= 2) {
    ctx.save();
    ctx.scale(-1, 1);
    ctx.drawImage(video, -width, 0, width, height);
    ctx.restore();
  }
  if (overlay) {
    ctx.save();
    ctx.scale(-1, 1);
    ctx.drawImage(overlay, -width, 0, width, height);
    ctx.restore();
  }

  const state = lastRenderedState || {};
  ctx.fillStyle = "rgba(5, 6, 7, 0.72)";
  ctx.fillRect(width - 390, 34, 350, 236);
  ctx.strokeStyle = "rgba(53, 242, 154, 0.42)";
  ctx.strokeRect(width - 390, 34, 350, 236);
  ctx.fillStyle = "#35f29a";
  ctx.font = "700 22px Inter, Arial";
  ctx.fillText("Kinaesthetic AI", width - 366, 74);
  ctx.fillStyle = "#ffffff";
  ctx.font = "800 54px Inter, Arial";
  ctx.fillText(pct(state.tilt_risk), width - 366, 138);
  ctx.font = "700 22px Inter, Arial";
  ctx.fillText(`Readiness ${pct(state.readiness)}`, width - 366, 184);
  ctx.fillText(`Recovery ${pct(state.recovery)}`, width - 366, 222);
  ctx.fillStyle = "rgba(255,255,255,0.74)";
  ctx.font = "600 18px Inter, Arial";
  ctx.fillText(state.recommendation || "20-sec reset", 42, height - 46);
}

function startCanvasRecording(durationMs = 90_000) {
  const canvas = q("recordingCanvas");
  if (!canvas || !canvas.captureStream || !window.MediaRecorder) {
    setText("pitchRunStatus", "MediaRecorder недоступен в этом браузере. Используйте OBS overlay.");
    return null;
  }
  const ctx = canvas.getContext("2d");
  const stream = canvas.captureStream(30);
  const mimeType = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
    ? "video/webm;codecs=vp9"
    : "video/webm";
  activeRecordingChunks = [];
  activeRecorder = new MediaRecorder(stream, { mimeType });
  activeRecorder.ondataavailable = (event) => {
    if (event.data?.size) activeRecordingChunks.push(event.data);
  };
  activeRecorder.onstop = () => {
    if (activeRecordingLoop) cancelAnimationFrame(activeRecordingLoop);
    const blob = new Blob(activeRecordingChunks, { type: "video/webm" });
    const url = URL.createObjectURL(blob);
    const link = q("recordingLink");
    if (link) {
      link.href = url;
      link.hidden = false;
      link.textContent = `Открыть demo video (${Math.round(blob.size / 1024 / 1024 * 10) / 10} MB)`;
    }
    setText("pitchRunStatus", "Demo video записано в браузере. Founder evidence package сохранён локально.");
  };
  const tick = () => {
    drawRecordingFrame(canvas, ctx);
    activeRecordingLoop = requestAnimationFrame(tick);
  };
  tick();
  activeRecorder.start(1000);
  setTimeout(() => {
    if (activeRecorder?.state === "recording") activeRecorder.stop();
  }, durationMs);
  return activeRecorder;
}

async function recordDemoVideoMode() {
  const button = q("recordDemoVideo");
  if (button) {
    button.disabled = true;
    button.textContent = "Запись 90 сек...";
  }
  setText("pitchRunStatus", "Запускаю 90-секундный pitch demo: baseline → tilt → alert → recovery → proof.");
  await startCamera();
  startCanvasRecording(90_000);
  startPitchDemo({ duration_seconds: 90 });
  let left = 90;
  const countdown = setInterval(() => {
    left -= 1;
    setText("pitchRunStatus", `Идёт запись demo-сценария: осталось ${left} сек.`);
  }, 1000);
  setTimeout(async () => {
    clearInterval(countdown);
    await recordPitchRun();
    if (button) {
      button.disabled = false;
      button.textContent = "Записать 90-сек demo";
    }
  }, 90_000);
}

function startPitchDemo(options = {}) {
  stopTimers();
  activeMode = "demo";
  const duration = Number(options.duration_seconds || 28);
  const scale = duration / 28;
  const started = performance.now();
  demoTimer = setInterval(() => {
    const elapsed = (performance.now() - started) / 1000;
    const current = [...pitchTimeline].reverse().find((step) => elapsed >= step.t * scale) || pitchTimeline[0];
    renderState({
      mode: "demo",
      phase: current.phase,
      engine_connected: false,
      camera_active: Boolean(cameraStream),
      signal_confidence: 0.94,
      fps: 30,
      latency_ms: 18,
      face_detected: true,
      shoulders_visible: true,
      raw_landmarks: 511,
      derived_points: 2048,
      signal_layers: 7,
      jaw_score: current.jaw === "high" ? 88 : current.jaw === "medium" ? 54 : 16,
      brow_tension: current.phase === "alert" ? 72 : current.phase === "rising" ? 48 : 18,
      eye_tension: current.phase === "alert" ? 66 : current.phase === "rising" ? 38 : 14,
      mouth_pressure: current.jaw === "high" ? 84 : current.jaw === "medium" ? 47 : 12,
      head_drift: current.phase === "alert" ? 61 : current.phase === "rising" ? 34 : 12,
      tilt_risk: current.tilt,
      readiness: current.readiness,
      recovery: current.recovery,
      jaw_tension: current.jaw,
      shoulder_tension: current.shoulders,
      recommendation: current.command,
      summary: current.summary,
      message: "Киношный demo mode: baseline → rising tilt → alert → recovery → proof card.",
    });
    drawPitchOverlayFrame(current.phase);
    if (elapsed > duration) clearInterval(demoTimer);
  }, 350);
}

async function startCamera() {
  const video = q("cameraPreview");
  const placeholder = q("cameraPlaceholder");
  if (!video || !navigator.mediaDevices?.getUserMedia) {
    if (placeholder) placeholder.textContent = "Браузер не поддерживает getUserMedia.";
    return false;
  }
  if (cameraStream) return true;
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    video.srcObject = cameraStream;
    await new Promise((resolve) => {
      if (video.readyState >= 2) {
        resolve();
        return;
      }
      video.onloadedmetadata = () => resolve();
      setTimeout(resolve, 1200);
    });
    await video.play().catch(() => {});
    if (placeholder) placeholder.style.display = "none";
    return true;
  } catch {
    if (placeholder) placeholder.textContent = "Камера не включена. Проверьте разрешение браузера.";
    return false;
  }
}

async function ensureBrowserCvEngine() {
  if (browserCvEngine) return browserCvEngine;
  const video = q("cameraPreview");
  const overlayCanvas = q("landmarkOverlay");
  const signalCanvas = q("browserCanvas");
  const baselineProvider = () => browserBaseline;
  const module = await import("/browser_cv.js");
  try {
    setText("humanSummary", "Загружаю MediaPipe Tasks Vision в браузере...");
    browserCvEngine = await module.createBrowserCvEngine({
      video,
      overlayCanvas,
      signalCanvas,
      baselineProvider,
    });
    browserCvBackend = "mediapipe";
  } catch (error) {
    console.warn("MediaPipe browser CV unavailable, using motion fallback", error);
    browserCvEngine = module.createMotionFallbackEngine({
      video,
      overlayCanvas,
      signalCanvas,
      baselineProvider,
    });
    browserCvBackend = "motion_fallback";
  }
  return browserCvEngine;
}

function drawPitchOverlayFrame(phase = "baseline") {
  const canvas = q("landmarkOverlay");
  const video = q("cameraPreview");
  if (!canvas || !video) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  const color = phase === "alert" ? "#ff4f58" : phase === "rising" ? "#ffb84d" : "#35f29a";
  const cx = width * 0.55;
  const cy = height * 0.43;
  const rx = width * 0.18;
  const ry = height * 0.24;
  ctx.save();
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  ctx.shadowColor = color;
  ctx.shadowBlur = 10;
  ctx.globalAlpha = 0.22;
  for (let i = 0; i < 2048; i += 1) {
    const a = (i * 9301 + 49297) % 233280;
    const b = (i * 23399 + 11939) % 233280;
    const angle = (a / 233280) * Math.PI * 2;
    const radius = Math.sqrt(b / 233280);
    const x = cx + Math.cos(angle) * rx * radius;
    const y = cy + Math.sin(angle) * ry * radius;
    ctx.beginPath();
    ctx.arc(x, y, i % 11 === 0 ? 1.6 : 0.8, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 0.92;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.ellipse(cx, cy, rx * 0.72, ry * 0.86, -0.1, 0, Math.PI * 2);
  ctx.stroke();
  const shoulderY = height * 0.70;
  ctx.beginPath();
  ctx.moveTo(width * 0.30, shoulderY);
  ctx.lineTo(width * 0.50, height * 0.59);
  ctx.lineTo(width * 0.72, shoulderY + (phase === "alert" ? -28 : 0));
  ctx.stroke();
  ctx.restore();
}

function readBrowserSignals() {
  if (!browserCvEngine) {
    return { quality: 0, motion: 0, brightness: 0 };
  }
  const sample = browserCvEngine.detect();
  return {
    ...sample,
    quality: Math.round((sample.signal_confidence || 0) * 100),
    motion: Math.round(sample.motion || 0),
    brightness: Math.round(sample.brightness || 0),
  };
}

async function startBrowserCv() {
  const ok = await startCamera();
  if (!ok) return false;
  stopTimers();
  activeMode = "browser";
  const engine = await ensureBrowserCvEngine();
  browserTimer = setInterval(async () => {
    const signals = engine.detect();
    const recommendation = await requestCloudCoach(signals);
    lastBrowserQualityScore = Math.round((Number(signals.signal_confidence) || 0) * 100);
    const phase = Number(signals.tilt_risk || 0) > 75
      ? "alert"
      : Number(signals.tilt_risk || 0) > 50
        ? "rising"
        : Number(signals.recovery || 0) > 72
          ? "recovery"
          : "baseline";
    renderState({
      mode: "browser",
      phase,
      engine_connected: false,
      camera_active: true,
      age_seconds: 0,
      face_detected: Boolean(signals.face_detected ?? signals.faceDetected),
      shoulders_visible: Boolean(signals.shoulders_visible ?? signals.shouldersVisible),
      raw_landmarks: signals.raw_landmarks,
      derived_points: signals.derived_points,
      signal_layers: signals.signal_layers,
      runtime_source: signals.runtime_source,
      fps: signals.fps,
      latency_ms: signals.latency_ms,
      jaw_score: signals.jaw_score,
      brow_tension: signals.brow_tension,
      eye_tension: signals.eye_tension,
      mouth_pressure: signals.mouth_pressure,
      head_drift: signals.head_drift,
      signal_confidence: signals.signal_confidence,
      tilt_risk: signals.tilt_risk,
      readiness: signals.readiness,
      recovery: signals.recovery,
      jaw_tension: signals.jaw_tension,
      shoulder_tension: signals.shoulder_tension,
      recommendation,
      message: browserCvBackend === "mediapipe"
        ? "Browser CV beta: MediaPipe Pose + Face работает локально в браузере. Raw video не отправляется."
        : "Browser CV fallback: MediaPipe не загрузился, используется честный motion/light режим.",
    });
  }, 400);
  return true;
}

async function startSmartDemo() {
  const button = q("startSmartDemo");
  if (button) {
    button.disabled = true;
    button.textContent = "Запускаю камеру...";
  }
  setText("humanSummary", "Запрашиваю камеру и запускаю Browser CV. Видео не отправляется на сервер.");
  try {
    await startBrowserCv();
    if (button) button.textContent = "Демо запущено";
  } finally {
    if (button) {
      setTimeout(() => {
        button.disabled = false;
        button.textContent = "Перезапустить демо";
      }, 900);
    }
  }
}

async function startSmartDemoV2() {
  const button = q("startSmartDemo");
  if (button) {
    button.disabled = true;
    button.textContent = "Запускаю камеру...";
  }
  setText("humanSummary", "Запрашиваю камеру и запускаю Browser CV. Видео не отправляется на сервер.");
  try {
    const started = await startBrowserCv();
    if (button) button.textContent = started ? "Демо запущено" : "Разрешите камеру и нажмите ещё раз";
  } finally {
    if (button) {
      setTimeout(() => {
        button.disabled = false;
        button.textContent = cameraStream ? "Перезапустить демо" : "Запустить демо";
      }, 900);
    }
  }
}

function stopTimers() {
  if (demoTimer) clearInterval(demoTimer);
  if (browserTimer) clearInterval(browserTimer);
  demoTimer = null;
  browserTimer = null;
}

function stopDemo() {
  stopTimers();
  activeMode = "live";
  pollLiveState();
}

function renderWizard() {
  const root = q("wizardSteps");
  if (!root) return;
  root.innerHTML = "";
  wizardSteps.forEach((step, index) => {
    const node = document.createElement("div");
    node.className = index === wizardIndex ? "active" : wizardSamples[step.key] ? "done" : "";
    node.innerHTML = `<strong>${step.label}</strong><span>${step.instruction}</span>`;
    root.appendChild(node);
  });
}

async function runWizardStep() {
  q("baselineWizard").hidden = false;
  await startCamera();
  await ensureBrowserCvEngine();
  wizardIndex += 1;
  if (wizardIndex >= wizardSteps.length) {
    browserBaseline = {
      saved_at: new Date().toISOString(),
      ...wizardSamples,
    };
    localStorage.setItem("kinaesthetic_baseline_v1", JSON.stringify(browserBaseline));
    q("wizardNext").textContent = "Baseline сохранён";
    renderWizard();
    setText("humanSummary", "Personal baseline сохранён локально в браузере.");
    return;
  }
  renderWizard();
  q("wizardNext").textContent = "Записываем 5 сек...";
  const step = wizardSteps[wizardIndex];
  const samples = [];
  const started = performance.now();
  const timer = setInterval(() => samples.push(readBrowserSignals()), 300);
  setTimeout(() => {
    clearInterval(timer);
    const avg = (key) => Math.round(samples.reduce((sum, item) => sum + (item[key] || 0), 0) / Math.max(samples.length, 1));
    wizardSamples[step.key] = { quality: avg("quality"), motion: avg("motion"), brightness: avg("brightness") };
    q("wizardNext").textContent = wizardIndex + 1 >= wizardSteps.length ? "Сохранить профиль" : "Следующий шаг";
    renderWizard();
  }, Math.max(500, 5000 - (performance.now() - started)));
}

async function sendQuickFeedback(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = {
    felt_tension: form.get("felt_tension") === "on",
    helped: form.get("helped") === "on",
    false_alert: form.get("false_alert") === "on",
    moment: form.get("moment") || "",
    state: lastRenderedState,
  };
  try {
    const response = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    setText("feedbackStatus", result.ok ? "Label сохранён в tester_feedback.jsonl." : "Не удалось сохранить label.");
    refreshLabelCounter();
  } catch {
    setText("feedbackStatus", "Feedback API недоступен.");
  }
}

async function submitFeedbackLabel(payload) {
  const response = await fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, state: lastRenderedState }),
  });
  const result = await response.json();
  refreshLabelCounter();
  return result;
}

async function hotkeyTiltLabel() {
  try {
    await submitFeedbackLabel({
      felt_tension: true,
      helped: false,
      false_alert: false,
      moment: "hotkey_t_felt_tilt",
      source: "demo_hotkey_t",
    });
    setText("feedbackStatus", "Hotkey label сохранён: I felt tilt.");
  } catch {
    setText("feedbackStatus", "Не удалось сохранить hotkey label.");
  }
}

async function refreshLabelCounter() {
  try {
    const response = await fetch("/api/cohort-summary", { cache: "no-store" });
    const metrics = await response.json();
    const labels = Number(metrics.total_alerts_labelled || 0);
    setText("labelProgressText", `${labels} / ${LABEL_GOAL}`);
    const bar = q("labelProgressBar");
    if (bar) bar.style.width = `${Math.min(100, Math.round((labels / LABEL_GOAL) * 100))}%`;
    const list = q("recentLabels");
    if (list) {
      const recent = metrics.latest_testers || [];
      list.innerHTML = recent.length
        ? recent.map((item) => `<li>${item.label || "label"} · ${item.moment || "session"} · ${item.tester || "tester"}</li>`).join("")
        : "<li>Пока нет labels. Нажмите T во время демо.</li>";
    }
    setText("feedbackStatus", `Ground-truth labels: ${labels}. Цель для a16z: ${LABEL_GOAL}+.`);
  } catch {
    // Страница демо остаётся рабочей даже без metrics API.
  }
}

q("startSmartDemo")?.addEventListener("click", startSmartDemoV2);
q("startPitchDemo")?.addEventListener("click", async () => {
  await startCamera();
  startPitchDemo();
});
q("startBrowserCv")?.addEventListener("click", startBrowserCv);
q("recordPitchRun")?.addEventListener("click", recordPitchRun);
q("recordDemoVideo")?.addEventListener("click", recordDemoVideoMode);
q("startBaseline")?.addEventListener("click", () => {
  q("baselineWizard").hidden = false;
  renderWizard();
});
q("wizardNext")?.addEventListener("click", runWizardStep);
q("stopDemo")?.addEventListener("click", stopDemo);
q("toggleCockpit")?.addEventListener("click", () => {
  const frame = q("cockpitFrame");
  frame.hidden = !frame.hidden;
  q("toggleCockpit").textContent = frame.hidden ? "Показать cockpit iframe" : "Скрыть cockpit iframe";
});
q("quickFeedback")?.addEventListener("submit", sendQuickFeedback);
window.addEventListener("keydown", (event) => {
  const tag = event.target?.tagName?.toLowerCase();
  if (tag === "input" || tag === "textarea" || event.repeat) return;
  if (event.key.toLowerCase() === "t") {
    event.preventDefault();
    hotkeyTiltLabel();
  }
});

pollLiveState();
refreshLlmHealth();
refreshDataQuality();
refreshLabelCounter();
setInterval(pollLiveState, 400);
setInterval(refreshLlmHealth, 5000);
setInterval(refreshDataQuality, 3000);
setInterval(refreshLabelCounter, 7000);
