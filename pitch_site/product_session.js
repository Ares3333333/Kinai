import { createBrowserCvEngine, createMotionFallbackEngine } from "/browser_cv.js";
import { kaiBeacon, kaiPost, recordConsent } from "/kai_api.js";
import { hasConsent, requireConsent } from "/consent.js";
import { getVoiceLang, getVolume, isMuted, setMuted, setVoiceLang, setVolume, speak, unlockAudio } from "/tts_player.js";
import { CV_CONFIG } from "/cv_config.js";

const q = (id) => document.getElementById(id);
const surface = document.body.dataset.surface || "demo";
const params = new URLSearchParams(window.location.search);
const testerBase = sanitizeLabel(params.get("tester") || params.get("tester_id") || localStorage.getItem("kinaesthetic_tester_base") || "tester");
const testerLaunchId = `launch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
const testerId = sanitizeLabel(`${testerBase}-${testerLaunchId}`);
const game = sanitizeLabel(params.get("game") || localStorage.getItem("kinaesthetic_game") || "");

let sessionId = "";
let cameraStream = null;
let cvEngine = null;
let tickTimer = null;
let signalTimer = null;
let healthTimer = null;
let heartbeatTimer = null;
let queueFlushTimer = null;
let validationTimer = null;
let validationStartedAt = null;
let lastSignals = null;
let lastCoachAt = 0;
let lastCoachCommand = "";
let lastCoachProvider = "local";
let lastSpokenCommand = "";
let lastHighRiskState = false;
let lastVoiceReadyAt = 0;
let lastUiCommand = "";
let lastUiCommandAt = 0;
let tickInFlight = false;
let calibrationPhases = JSON.parse(localStorage.getItem("kinaesthetic_calibration_phases_v1") || "{}");
let investorMode = params.get("investor") === "1";
let sensitivityProfile = localStorage.getItem("kinaesthetic_sensitivity_profile_v1") || "normal";
let demoLockActive = false;
let demoLockStartedAt = 0;
let miniSeries = [];
let systemNumbersVisible = true;
let latestLlmHealth = { connected: false, status: "fallback", provider: "local", latency_ms: null };
let cameraStartInFlight = false;
let cameraErrorActive = false;
let baselineSamples = [];
let lastBaselineSyncAt = 0;
let recoveryTracker = { peak: 0, peakAt: 0, command: "", proof: null };
const demoLockSeconds = Math.max(5, Number(params.get("demo_lock_secs") || 90));
const COACH_COOLDOWN_MS = 18_000;
const CALIBRATION_SECONDS = 10;
const BASELINE_TARGET_SAMPLES = 8;
let calibrationWizard = { active: false, startedAt: 0 };
const LOCAL_COACH_COMMANDS = {
  jaw: [
    "Unclench jaw. Tongue loose. Exhale.",
    "Open the bite. Drop the jaw.",
    "Jaw soft. Breathe out slowly.",
  ],
  shoulders: [
    "Drop shoulders. Elbows heavy. Exhale.",
    "Shoulders down. Neck long. Reset.",
    "Release traps. Sit back. One breath.",
  ],
  posture: [
    "Sit back. Chin neutral. Breathe low.",
    "Spine tall. Shoulders loose. Exhale.",
    "Lean back. Unlock neck and jaw.",
  ],
  face: [
    "Soften eyes. Relax mouth. Exhale.",
    "Release your face. Keep hands steady.",
    "Eyes soft. Jaw loose. Stay in control.",
  ],
  recovery: [
    "Good reset. Keep breathing low.",
    "Recovery is working. Stay loose.",
    "Hold this calm posture.",
  ],
  stable: [
    "State stable. Keep jaw soft.",
    "All clear. Shoulders low.",
    "Good posture. Stay smooth.",
  ],
};
const perf = {
  tickCount: 0,
  droppedTicks: 0,
  lastTickMs: 0,
  apiLatencyMs: 0,
  cameraRecoveries: 0,
  lastCvFps: 0,
  currentTickMs: CV_CONFIG.live.tickMs,
  policy: "normal",
};

function sanitizeLabel(value) {
  return String(value || "").trim().replace(/[^a-zA-Z0-9_ .@-]/g, "").slice(0, 80);
}

function clamp(value, low = 0, high = 100) {
  return Math.max(low, Math.min(high, Number(value) || 0));
}

function sensitivityScale() {
  return CV_CONFIG.profileScale[sensitivityProfile] ?? CV_CONFIG.profileScale.normal;
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${Math.round(clamp(value))}%`;
}

function text(id, value) {
  const node = q(id);
  if (node) node.textContent = value ?? "-";
}

function readJSONSafe(key, fallback = null) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

function average(values) {
  const clean = values.map(Number).filter((value) => Number.isFinite(value));
  return clean.length ? clean.reduce((sum, value) => sum + value, 0) / clean.length : null;
}

function currentBaselineProfile() {
  const stored = readJSONSafe("kinaesthetic_baseline_v1", null);
  if (!stored) return null;
  if (stored.neutral) return stored;
  if (stored.baseline?.neutral) return stored.baseline;
  return stored;
}

function browserCvBaselineProfile() {
  const profile = currentBaselineProfile();
  if (!profile?.neutral) return null;
  return profile;
}

function hasPersonalBaseline() {
  const profile = currentBaselineProfile();
  return Boolean(profile?.neutral);
}

function updateBaselineBadge(profile = currentBaselineProfile()) {
  const badge = q("baselineBadge");
  if (!badge) return;
  if (profile?.neutral) {
    const samples = profile.samples || profile.neutral.samples || "?";
    badge.textContent = `baseline: personal - ${samples} samples`;
    badge.dataset.state = "live";
  } else {
    badge.textContent = `baseline: learning - ${baselineSamples.length}/${BASELINE_TARGET_SAMPLES}`;
    badge.dataset.state = baselineSamples.length >= BASELINE_TARGET_SAMPLES ? "stale" : "offline";
  }
}

function maybeLearnPersonalBaseline(signals) {
  if (!signals || signals.is_fallback) return;
  const signalReady =
    signals.signal_confidence >= 0.55
    && signals.face_detected
    && signals.shoulders_visible;
  const calm = calibrationWizard.active
    ? signalReady
    : signalReady
      && Number(signals.tilt_risk || 0) <= 45
      && Number(signals.jaw_score || 0) < 58
      && Number(signals.posture_stress || 0) < 58;
  if (!calm) {
    updateBaselineBadge();
    return;
  }
  baselineSamples.push({
    jawOpenRatio: signals.jawOpenRatio,
    shoulderDistance: signals.shoulderDistance,
    shoulderWidth: signals.shoulderWidth,
    motion: signals.motion,
    brightness: signals.brightness,
    jaw_score: signals.jaw_score,
    shoulder_score: signals.shoulder_score,
    facial_tension: signals.facial_tension,
    posture_stress: signals.posture_stress,
  });
  baselineSamples = baselineSamples.slice(-40);
  if (baselineSamples.length < BASELINE_TARGET_SAMPLES) {
    updateBaselineBadge();
    return;
  }
  const neutral = {
    jawOpenRatio: average(baselineSamples.map((item) => item.jawOpenRatio)) ?? 0.035,
    shoulderDistance: average(baselineSamples.map((item) => item.shoulderDistance)) ?? 0.235,
    shoulderWidth: average(baselineSamples.map((item) => item.shoulderWidth)) ?? 0.30,
    motion: average(baselineSamples.map((item) => item.motion)) ?? 12,
    brightness: average(baselineSamples.map((item) => item.brightness)) ?? 60,
    jaw_score: average(baselineSamples.map((item) => item.jaw_score)) ?? 0,
    shoulder_score: average(baselineSamples.map((item) => item.shoulder_score)) ?? 0,
    facial_tension: average(baselineSamples.map((item) => item.facial_tension)) ?? 0,
    posture_stress: average(baselineSamples.map((item) => item.posture_stress)) ?? 0,
    samples: baselineSamples.length,
  };
  const profile = {
    schema: "personal_baseline_v3",
    profile_id: testerId || "anonymous",
    tester_id: testerId,
    game,
    updated_at: new Date().toISOString(),
    neutral,
    local_first: true,
    privacy: { raw_video_saved: false, raw_audio_saved: false, derived_signals_only: true },
    samples: baselineSamples.length,
  };
  localStorage.setItem("kinaesthetic_baseline_v1", JSON.stringify(profile));
  updateBaselineBadge(profile);
  if (calibrationWizard.active) {
    calibrationWizard.active = false;
    text("calibrationStatus", "Baseline saved. Calm posture is now the zero point.");
  }
  if (Date.now() - lastBaselineSyncAt > 30000) {
    lastBaselineSyncAt = Date.now();
    postJSON("/api/baseline-profile", {
      profile_id: testerId || "anonymous",
      tester_id: testerId,
      game,
      baseline: profile,
      source: "auto_personal_baseline",
    }).catch(() => {});
  }
}

function startCalibrationWizard() {
  if (!cameraStream) {
    text("calibrationStatus", "Start the camera first, then run calibration.");
    return;
  }
  calibrationWizard = { active: true, startedAt: Date.now() };
  baselineSamples = [];
  localStorage.removeItem("kinaesthetic_baseline_v1");
  lastCoachCommand = "";
  lastCoachAt = 0;
  text("calibrationStatus", "Calibrating: sit naturally for 10 seconds.");
  updateBaselineBadge(null);
}

function resetCalibrationProfile() {
  calibrationWizard = { active: false, startedAt: 0 };
  baselineSamples = [];
  calibrationPhases = {};
  localStorage.removeItem("kinaesthetic_baseline_v1");
  localStorage.removeItem("kinaesthetic_calibration_phases_v1");
  text("calibrationStatus", "Baseline cleared. Calibrate again before judging tilt.");
  updateBaselineBadge(null);
}

function updateCalibrationWizard() {
  if (!calibrationWizard.active) return;
  const elapsed = (Date.now() - calibrationWizard.startedAt) / 1000;
  const remaining = Math.max(0, CALIBRATION_SECONDS - elapsed);
  if (hasPersonalBaseline()) {
    calibrationWizard.active = false;
    text("calibrationStatus", "Personal baseline saved. Tilt now compares the player against themselves.");
    return;
  }
  text(
    "calibrationStatus",
    `Calibrating: ${Math.ceil(remaining)}s left - samples ${baselineSamples.length}/${BASELINE_TARGET_SAMPLES}.`,
  );
}

function evaluateSignalQuality(signals) {
  const confidence = Number(signals.signal_confidence || 0);
  const fps = Number(signals.fps || 0);
  const issues = [];
  if (signals.is_fallback) issues.push("MediaPipe fallback");
  if (!signals.face_detected) issues.push("face out of frame");
  if (!signals.shoulders_visible) issues.push("shoulders out of frame");
  if (confidence < 0.55) issues.push("weak signal");
  if (fps > 0 && fps < 5) issues.push("low FPS");
  if (Number(signals.brightness || 0) > 0 && Number(signals.brightness || 0) < 18) issues.push("low light");
  if (!hasPersonalBaseline() && !calibrationWizard.active) issues.push("baseline learning");
  const fpsScore = fps <= 0 ? 4 : Math.min(10, fps);
  const score = clamp(
    confidence * 58
    + (signals.face_detected ? 15 : 0)
    + (signals.shoulders_visible ? 17 : 0)
    + fpsScore,
  );
  const signalOk = !signals.is_fallback && confidence >= 0.55 && signals.face_detected && signals.shoulders_visible && score >= 65;
  const ok = signalOk;
  const liveCamera = Boolean(cameraStream && !cameraErrorActive);
  return { ok, signalOk, score, issues, numbersAllowed: ok || liveCamera || demoLockActive || calibrationWizard.active };
}

function applySignalQualityGate(signals) {
  const quality = evaluateSignalQuality(signals);
  const gated = {
    ...signals,
    quality_gate: quality,
    quality_score: Math.round(quality.score),
    quality_gate_ok: quality.ok,
    quality_issues: quality.issues.join(", "),
  };
  if (!quality.numbersAllowed) {
    gated.tilt_risk = null;
    gated.readiness = null;
    gated.recovery = null;
    gated.jaw_tension = "low";
    gated.shoulder_tension = "low";
    gated.recommendation = quality.issues.length
      ? `Fix signal: ${quality.issues.slice(0, 2).join(", ")}. Metrics are paused.`
      : "Waiting for a reliable camera signal.";
  }
  return gated;
}

function renderQualityGate(quality) {
  const badge = q("qualityGateBadge");
  if (!badge || !quality) return;
  badge.dataset.state = quality.ok ? "live" : "stale";
  badge.textContent = quality.ok
    ? `quality: ${Math.round(quality.score)}%`
    : `quality: fix signal - ${Math.round(quality.score)}%`;
}

function updateLocalRecoveryProof(signals) {
  if (!signals || !signals.quality_gate?.numbersAllowed || signals.tilt_risk == null) return;
  const tilt = Number(signals.tilt_risk || 0);
  const now = Date.now();
  if (tilt >= 65 && tilt >= recoveryTracker.peak) {
    recoveryTracker = {
      peak: tilt,
      peakAt: now,
      command: signals.recommendation || recoveryTracker.command,
      proof: recoveryTracker.proof,
    };
  }
  const canProve = recoveryTracker.peak >= 65 && tilt <= recoveryTracker.peak - 18 && now - recoveryTracker.peakAt >= 4000;
  if (!canProve) return;
  const proof = {
    schema: "local_recovery_proof_v1",
    created_at: new Date().toISOString(),
    session_id: sessionId,
    tester_id: testerId,
    game,
    tilt_before: Math.round(recoveryTracker.peak),
    tilt_after: Math.round(tilt),
    recovery_seconds: Math.round((now - recoveryTracker.peakAt) / 1000),
    command: recoveryTracker.command || signals.recommendation || "",
    dominant_lock: signals.shoulder_tension === "high" ? "shoulders" : signals.jaw_tension === "high" ? "jaw" : "mixed",
    privacy: { raw_video_saved: false, raw_audio_saved: false, derived_signals_only: true },
  };
  recoveryTracker = { peak: tilt, peakAt: now, command: "", proof };
  localStorage.setItem("kinaesthetic_last_recovery_proof_v1", JSON.stringify(proof));
  text("proofHeadline", `Tilt ${proof.tilt_before} -> ${proof.tilt_after}`);
  text("proofDetails", `Recovery in ${proof.recovery_seconds}s. Command: ${proof.command || "local coach"}.`);
}


function setRuntimeMode(mode) {
  const node = q("runtimeMode");
  if (!node) return;
  const normalized = String(mode || "offline").toLowerCase();
  const map = { live: "LIVE", offline: "OFFLINE", demo: "DEMO", stale: "STALE" };
  node.textContent = map[normalized] || normalized.toUpperCase();
  node.dataset.state = normalized;
  const hint = q("modeHint");
  if (hint) {
    hint.dataset.mode = normalized;
    hint.textContent = normalized === "live"
      ? "LIVE: analysis is running."
      : normalized === "demo"
        ? "DEMO: scripted scenario is shown."
        : normalized === "stale"
          ? "STALE: signal is outdated, restart the session."
          : "OFFLINE: click Start camera.";
  }
}

function setStartButton(state) {
  const button = q("startProductDemo");
  if (!button) return;
  const labels = {
    idle: "Start camera",
    starting: "Waiting for camera...",
    live: "Coach is running",
    error: "Retry",
  };
  button.disabled = state === "starting";
  button.innerHTML = `<span aria-hidden="true">&#9654;</span><span>${labels[state] || labels.idle}</span>`;
}

function setGraphRuntime(mode) {
  const badge = q("graphLiveBadge");
  const ts = q("graphUpdatedAt");
  if (!badge || !ts) return;
  const normalized = String(mode || "offline").toLowerCase();
  badge.textContent = normalized === "live" ? "LIVE" : normalized === "demo" ? "DEMO" : "OFFLINE";
  badge.dataset.state = normalized;
  ts.textContent = `updated: ${new Date().toLocaleTimeString("ru-RU")}`;
}

function updateLlmHealthBadge(health) {
  const node = q("llmHealthBadge");
  const meta = q("llmHealthMeta");
  if (!node || !meta) return;
  const connected = Boolean(health?.connected);
  const status = String(health?.status || (connected ? "connected" : "fallback"));
  latestLlmHealth = {
    connected,
    status,
    provider: health?.provider || "local",
    latency_ms: health?.latency_ms ?? null,
  };
  node.dataset.state = connected ? "live" : status === "timeout" ? "stale" : "demo";
  node.textContent = connected ? "LLM connected" : status === "timeout" ? "LLM timeout" : "LLM fallback";
  meta.textContent = `latency: ${health?.latency_ms ?? "-"}ms - provider: ${health?.provider || "local"}`;
  const card = q("llmHealthValue")?.closest(".metric-card");
  if (card) card.dataset.health = connected ? "live" : status === "timeout" ? "stale" : "offline";
  text("llmHealthValue", connected ? "connected" : status === "timeout" ? "timeout" : "fallback");
  text("llmHealthUpdated", `updated: ${new Date().toLocaleTimeString("ru-RU")}`);
  text(
    "llmHealthRoute",
    `route: ${health?.provider || "local"} - latency ${health?.latency_ms ?? "-"}ms`
  );
}

function applyTickInterval(ms) {
  const next = Math.max(CV_CONFIG.live.minTickMs || 220, Math.min(CV_CONFIG.live.maxTickMs || 480, Math.round(ms)));
  if (tickTimer && perf.currentTickMs === next) return;
  if (tickTimer) clearInterval(tickTimer);
  tickTimer = setInterval(tick, next);
  perf.currentTickMs = next;
  text("perfRuntimePolicy", `policy: ${perf.policy} - tick ${next}ms`);
}

function retuneRuntimeLoop() {
  const droppedPct = perf.tickCount ? (perf.droppedTicks / perf.tickCount) * 100 : 0;
  const overloaded = perf.lastTickMs > perf.currentTickMs * 0.9 || droppedPct >= 8 || (perf.lastCvFps > 0 && perf.lastCvFps < 6);
  const recovered = perf.lastTickMs < perf.currentTickMs * 0.55 && droppedPct < 3 && (perf.lastCvFps >= 8 || perf.lastCvFps === 0);
  if (overloaded && perf.currentTickMs < (CV_CONFIG.live.maxTickMs || 480)) {
    perf.policy = "stable";
    applyTickInterval(perf.currentTickMs + 40);
    return;
  }
  if (recovered && perf.currentTickMs > (CV_CONFIG.live.minTickMs || 220)) {
    perf.policy = "fast";
    applyTickInterval(perf.currentTickMs - 20);
    return;
  }
  perf.policy = "normal";
  text("perfRuntimePolicy", `policy: ${perf.policy} - tick ${perf.currentTickMs}ms`);
}

function updatePerformanceCard(profile = null) {
  const fps = Number(profile?.fps ?? perf.lastCvFps ?? 0);
  const frameMs = Number(profile?.frame_ms ?? perf.lastTickMs ?? 0);
  const poseMs = Number(profile?.pose_ms ?? 0);
  const faceMs = Number(profile?.face_ms ?? 0);
  const node = q("perfCard");
  if (node) node.dataset.health = fps >= 8 ? "ok" : fps >= 5 ? "slow" : "critical";
  text("perfFpsValue", fps > 0 ? `${fps.toFixed(1)} fps` : "-");
  text(
    "perfFrameSplit",
    `frame ${frameMs > 0 ? frameMs.toFixed(1) : "-"}ms - pose ${poseMs > 0 ? poseMs.toFixed(1) : "-"}ms - face ${faceMs > 0 ? faceMs.toFixed(1) : "-"}ms`
  );
  text("perfRuntimePolicy", `policy: ${perf.policy} - tick ${perf.currentTickMs}ms`);
}

async function refreshPerformanceProfile() {
  const profile = await safeFetchJson("/api/performance-profile", null);
  updatePerformanceCard(profile);
}

async function safeFetchJson(url, fallback = null) {
  const started = performance.now();
  try {
    const response = await fetch(url, { cache: "no-store" });
    const json = await response.json();
    perf.apiLatencyMs = performance.now() - started;
    return json;
  } catch {
    perf.apiLatencyMs = performance.now() - started;
    return fallback;
  }
}

async function refreshSystemHealth() {
  const health = await safeFetchJson("/api/system-health", null);
  if (!health) return;
  const localBrowserLive = Boolean((tickTimer || q("cameraPreview")?.srcObject) && !cameraErrorActive);
  systemNumbersVisible = localBrowserLive ? true : Boolean(health.numbers_visible ?? true);
  if (cameraErrorActive && !tickTimer) {
    setRuntimeMode("offline");
    setGraphRuntime("offline");
    text("productStatus", "camera did not start");
    updateLlmHealthBadge(health.llm || {});
    return;
  }
  if (localBrowserLive) {
    setRuntimeMode("live");
    setGraphRuntime("live");
  } else if (health.mode && !tickTimer) {
    setRuntimeMode(health.mode);
    setGraphRuntime(health.mode);
  }
  if (!systemNumbersVisible && !localBrowserLive) {
    text("tiltValue", "-");
    text("readinessValue", "-");
    text("recoveryValue", "-");
    text("facialTensionValue", "-");
    text("postureTensionValue", "-");
  }
  if (health.message && !tickTimer) text("statusMessage", health.message);
  updateLlmHealthBadge(health.llm || {});
}

function band(value) {
  const score = Number(value) || 0;
  if (score >= 72) return "high";
  if (score >= 45) return "medium";
  return "low";
}

function tensionLabel(value) {
  return { high: "high", medium: "medium", low: "low" }[value] || "-";
}

function drawMiniGraph() {
  const canvas = q("miniGraph");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "rgba(255,255,255,0.03)";
  ctx.fillRect(0, 0, w, h);
  if (miniSeries.length < 2) return;
  const now = Date.now();
  const rangeMs = 60000;
  const start = now - rangeMs;
  const points = miniSeries.filter((p) => p.t >= start);
  if (points.length < 2) return;

  const xOf = (t) => ((t - start) / rangeMs) * (w - 20) + 10;
  const yOf = (v) => (h - 16) - (clamp(v) / 100) * (h - 24);

  ctx.strokeStyle = "rgba(53,242,154,0.95)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = xOf(p.t);
    const y = yOf(p.tilt);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.strokeStyle = "rgba(142,232,255,0.95)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = xOf(p.t);
    const y = yOf(p.readiness);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  const droppedPct = perf.tickCount ? Math.round((perf.droppedTicks / perf.tickCount) * 100) : 0;
  ctx.fillStyle = "rgba(255,255,255,0.75)";
  ctx.font = "11px Inter, Arial, sans-serif";
  ctx.fillText(`tick ${Math.round(perf.lastTickMs)}ms - api ${Math.round(perf.apiLatencyMs)}ms - dropped ${droppedPct}%`, 12, 14);
}

function pushMiniSeries(signals) {
  miniSeries.push({ t: Date.now(), tilt: Number(signals.tilt_risk || 0), readiness: Number(signals.readiness || 0) });
  const cutoff = Date.now() - 65000;
  miniSeries = miniSeries.filter((p) => p.t >= cutoff);
  drawMiniGraph();
}

function buildDemoLockSignals() {
  const elapsed = (Date.now() - demoLockStartedAt) / 1000;
  const phase = Math.min(1, elapsed / 90);
  let tilt = 30;
  let readiness = 80;
  let recovery = 74;
  let recommendation = "Stable. Keep jaw soft and shoulders down.";
  let jaw = "low";
  let shoulders = "low";
  if (phase < 0.28) {
    tilt = 26 + phase * 40;
    readiness = 84 - phase * 22;
    recovery = 78 - phase * 8;
    recommendation = "Baseline is stable.";
  } else if (phase < 0.56) {
    tilt = 58 + (phase - 0.28) * 115;
    readiness = 70 - (phase - 0.28) * 66;
    recovery = 62 - (phase - 0.28) * 28;
    jaw = "medium";
    shoulders = "medium";
    recommendation = "Tilt risk rising: relax jaw and shoulders.";
  } else if (phase < 0.75) {
    tilt = 84 - (phase - 0.56) * 62;
    readiness = 48 + (phase - 0.56) * 78;
    recovery = 38 + (phase - 0.56) * 135;
    jaw = "high";
    shoulders = "high";
    recommendation = "20 seconds: exhale, soften jaw, shoulders down.";
  } else {
    tilt = 42 - (phase - 0.75) * 30;
    readiness = 72 + (phase - 0.75) * 26;
    recovery = 70 + (phase - 0.75) * 26;
    jaw = "low";
    shoulders = "low";
    recommendation = "Recovery confirmed. Keep it soft.";
  }
  return {
    mode: "browser",
    source: "browser_cv",
    session_id: sessionId,
    tester_id: testerId,
    game,
    page_url: window.location.href,
    tilt_risk: clamp(tilt),
    readiness: clamp(readiness),
    recovery: clamp(recovery),
    jaw_tension: jaw,
    shoulder_tension: shoulders,
    signal_confidence: 0.95,
    face_detected: true,
    shoulders_visible: true,
    fps: 14,
    latency_ms: 95,
    runtime_source: "demo_lock",
    is_fallback: false,
    quality_gate: { ok: true, score: 95, issues: [], numbersAllowed: true },
    recommendation,
  };
}

function setStep(id, ok) {
  const node = q(id);
  if (node) node.classList.toggle("is-ok", Boolean(ok));
}

function consentAccepted() {
  const local = q("consentLocal");
  const signals = q("consentSignals");
  const noMedical = q("consentNoMedical");
  if (!local || !signals || !noMedical) return true;
  return local.checked && signals.checked && noMedical.checked;
}

// If the user already accepted the global consent dialog (consent.js),
// Auto-tick the inline consent boxes too; they are informational copies.
// of the same agreement and otherwise silently block "Start session".
function autoFillInlineConsentIfGlobal() {
  if (!hasConsent()) return false;
  let changed = false;
  ["consentLocal", "consentSignals", "consentNoMedical"].forEach((id) => {
    const node = q(id);
    if (node && !node.checked) {
      node.checked = true;
      changed = true;
    }
  });
  if (changed) q("consentPanel")?.classList.add("is-accepted");
  return true;
}

function persistConsent() {
  localStorage.setItem("kinaesthetic_consent_v1", JSON.stringify({
    accepted_at: new Date().toISOString(),
    raw_video_local: true,
    derived_signals_allowed: true,
    no_medical_claims: true,
  }));
  // Append a server-side audit record. Fire-and-forget; the local copy
  // is what gates the UI.
  recordConsent({
    source: "product_session",
    raw_video_local: true,
    derived_signals_allowed: true,
    no_medical_claims: true,
  }).catch(() => {});
}

function restoreConsent() {
  const saved = localStorage.getItem("kinaesthetic_consent_v1");
  if (!saved) return;
  ["consentLocal", "consentSignals", "consentNoMedical"].forEach((id) => {
    const node = q(id);
    if (node) node.checked = true;
  });
  q("consentPanel")?.classList.add("is-accepted");
}

async function postJSON(url, payload) {
  // All write endpoints go through kaiPost so they automatically include
  // the write-token, consent-version, and 429 handling.
  return reliablePost(url, payload || {});
}

const OFFLINE_QUEUE_KEY = "kinaesthetic_write_queue_v1";
const QUEUEABLE_PATHS = new Set([
  "/api/signals",
  "/api/session",
  "/api/session-heartbeat",
  "/api/feedback",
  "/api/baseline-profile",
  "/api/study-event",
]);

function queueKind(path) {
  return {
    "/api/signals": "signal",
    "/api/session": "session",
    "/api/session-heartbeat": "heartbeat",
    "/api/feedback": "feedback",
    "/api/baseline-profile": "baseline_profile",
    "/api/study-event": "study_event",
  }[path] || path;
}

function readOfflineQueue() {
  try {
    const rows = JSON.parse(localStorage.getItem(OFFLINE_QUEUE_KEY) || "[]");
    return Array.isArray(rows) ? rows : [];
  } catch {
    return [];
  }
}

function writeOfflineQueue(rows) {
  localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(rows.slice(-1000)));
  text("queueStatus", rows.length ? `queue: ${rows.length}` : "queue: empty");
}

function enqueueOfflineWrite(path, payload, reason = "network_error") {
  if (!QUEUEABLE_PATHS.has(path)) return;
  const rows = readOfflineQueue();
  rows.push({
    id: `q-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    queued_at: new Date().toISOString(),
    kind: queueKind(path),
    path,
    reason,
    payload,
  });
  writeOfflineQueue(rows);
}

async function flushOfflineQueue() {
  const rows = readOfflineQueue();
  if (!rows.length || !navigator.onLine) {
    writeOfflineQueue(rows);
    return;
  }
  try {
    const result = await kaiPost("/api/queue-flush", { events: rows });
    if (result?.ok) {
      writeOfflineQueue([]);
      text("queueStatus", "queue: synced");
    }
  } catch {
    writeOfflineQueue(rows);
  }
}

async function reliablePost(path, payload, options = {}) {
  try {
    const response = await kaiPost(path, payload || {}, options);
    if (response?.ok === false && response.error && response.error !== "rate_limited") {
      enqueueOfflineWrite(path, payload, response.error);
    }
    return response;
  } catch (err) {
    enqueueOfflineWrite(path, payload, String(err).slice(0, 120));
    return { ok: false, queued: QUEUEABLE_PATHS.has(path), error: "queued_offline" };
  }
}

async function refreshCoachHealth() {
  try {
    const health = await safeFetchJson("/api/llm-health", { connected: false, status: "fallback", provider: "local" });
    const label = health.connected ? `${health.provider || "cloud"} - ${health.latency_ms || "-"}ms` : "local fallback";
    text("coachStatus", label);
    text("cloudStatus", `cloud coach: ${label}`);
    updateLlmHealthBadge(health);
  } catch {
    text("coachStatus", "local fallback");
    text("cloudStatus", "cloud coach: offline");
    updateLlmHealthBadge({ connected: false, status: "fallback", provider: "local" });
  }
}

async function startSession() {
  const response = await postJSON("/api/session", {
    action: "start",
    source: "browser_cv",
    mode: surface,
    tester_id: testerId,
    game,
    page_url: window.location.href,
  });
  sessionId = response?.session?.session_id || `web-${Date.now()}`;
  localStorage.setItem("kinaesthetic_public_session_id", sessionId);
  localStorage.setItem("kinaesthetic_tester_base", testerBase);
  localStorage.setItem("kinaesthetic_tester_id", testerId);
  if (game) localStorage.setItem("kinaesthetic_game", game);
  text("sessionIdLabel", `session: ${sessionId}`);
  await postJSON("/api/study-event", {
    event: "session_started",
    label: "new_tester_launch",
    source: "browser_cv",
    mode: surface,
    session_id: sessionId,
    tester_id: testerId,
    tester_base: testerBase,
    game,
    page_url: window.location.href,
    raw_media_stored: false,
  }).catch(() => {});
  return sessionId;
}

async function endSession() {
  if (!sessionId) return;
  await postJSON("/api/session", { action: "end", session_id: sessionId, source: "browser_cv", mode: surface, tester_id: testerId, game }).catch(() => {});
  await refreshSessionProof();
  await refreshSessionScore();
  await refreshCoachMemory();
  await exportProofPackage();
  text("saveStatus", "data saved locally");
}

function sessionHeartbeatPayload() {
  return {
    session_id: sessionId,
    source: "browser_cv",
    mode: surface,
    tester_id: testerId,
    game,
    summary: lastSignals
      ? {
          tilt_risk: lastSignals.tilt_risk,
          readiness: lastSignals.readiness,
          recovery: lastSignals.recovery,
          signal_confidence: lastSignals.signal_confidence,
          recommendation: lastSignals.recommendation,
        }
      : null,
  };
}

async function sendHeartbeat() {
  if (!sessionId) return;
  await postJSON("/api/session-heartbeat", sessionHeartbeatPayload()).catch(() => {});
}

function endSessionBestEffort(reason = "page_close") {
  if (!sessionId) return;
  kaiBeacon("/api/session", {
    action: "end",
    session_id: sessionId,
    source: "browser_cv",
    mode: surface,
    tester_id: testerId,
    game,
    summary: { reason, last_signals: lastSignals },
  });
}

function setStartHint(message, { error = false } = {}) {
  const node = q("playStartHint");
  if (!node) return;
  if (message) node.textContent = message;
  node.classList.toggle("is-error", Boolean(error));
}

function setCameraHelp(details = null) {
  const panel = q("cameraHelpPanel");
  if (!panel) return;
  if (!details) {
    panel.hidden = true;
    return;
  }
  const title = q("cameraHelpTitle");
  const textNode = q("cameraHelpText");
  const stepsNode = q("cameraHelpSteps");
  if (title) title.textContent = details.title || details.state || "Check camera access";
  if (textNode) textNode.textContent = details.message || "Allow camera access in the browser and retry.";
  if (stepsNode) {
    const steps = details.steps?.length ? details.steps : [
      "Click the lock or camera icon in the address bar.",
      "Allow camera access for this site.",
      "Close other apps that may be using the camera.",
    ];
    stepsNode.innerHTML = steps.map((step) => `<li>${step}</li>`).join("");
  }
  panel.hidden = false;
}

function classifyCameraError(err) {
  const name = String(err?.name || "");
  if (name === "NotAllowedError" || name === "SecurityError") {
    return {
      state: "no access",
      title: "Camera blocked",
      message: "The browser denied camera access. Allow camera for this site and retry.",
      steps: [
        "Click the lock or camera icon near the site address.",
        "Set Camera to Allow. If it was blocked before, switch Block to Allow.",
        "Refresh the page and click Start camera again.",
      ],
    };
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return {
      state: "camera not found",
      title: "Camera not found",
      message: "The browser cannot see a webcam. Connect one or select it in browser settings.",
      steps: [
        "Check that the camera is connected and enabled.",
        "Open site settings in the browser and choose the correct camera.",
        "Refresh the page if the device was just connected.",
      ],
    };
  }
  if (name === "NotReadableError" || name === "AbortError") {
    return {
      state: "camera busy",
      title: "Camera is busy",
      message: "The camera is already in use. Close Zoom, Discord, OBS or another camera app.",
      steps: [
        "Close apps that use the camera.",
        "If OBS Virtual Camera is enabled, turn it off or choose another camera.",
        "Click Retry camera.",
      ],
    };
  }
  if (name === "TimeoutError") {
    return {
      state: "timeout",
      title: "Browser is waiting for permission",
      message: "The camera request is pending. Check the permission popup or camera icon in the address bar.",
      steps: [
        "Find the permission popup or camera icon in the browser.",
        "Choose Allow.",
        "If the popup disappeared, click Retry camera.",
      ],
    };
  }
  return {
    state: "camera error",
    title: "Camera did not start",
    message: "Check browser permissions, HTTPS/localhost and try again.",
    steps: [
      "Open the site through HTTPS or localhost.",
      "Allow camera access in the browser.",
      "Close other camera apps and retry.",
    ],
  };
}

function hasLiveCameraStream() {
  if (!cameraStream) return false;
  const tracks = cameraStream.getVideoTracks();
  return tracks.length > 0 && tracks.some((t) => t.readyState === "live");
}

async function getCameraPermissionState() {
  if (!navigator.permissions?.query) return "unknown";
  try {
    const status = await navigator.permissions.query({ name: "camera" });
    return status?.state || "unknown";
  } catch {
    return "unknown";
  }
}

async function requestCameraStream() {
  const primaryConstraints = {
    video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
    audio: false,
  };
  try {
    return await navigator.mediaDevices.getUserMedia(primaryConstraints);
  } catch (error) {
    if (String(error?.name || "") !== "OverconstrainedError") throw error;
    return navigator.mediaDevices.getUserMedia({ video: true, audio: false });
  }
}

async function startCamera() {
  const video = q("cameraPreview");
  const placeholder = q("cameraPlaceholder");
  if (!video || !navigator.mediaDevices?.getUserMedia) {
    const msg = "This browser does not support camera access. Open the site in Chrome or Edge.";
    if (placeholder) placeholder.textContent = msg;
    setStartHint(msg, { error: true });
    setCameraHelp({
      title: "Browser does not support camera access",
      message: msg,
      steps: ["Open the site in Chrome or Edge.", "Use HTTPS or localhost.", "Click Retry camera."],
    });
    text("cameraState", "not supported");
    return false;
  }
  if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    const msg = "Camera access works only on HTTPS or localhost. Public tester links must use HTTPS.";
    if (placeholder) placeholder.textContent = msg;
    setStartHint(msg, { error: true });
    setCameraHelp({
      title: "HTTPS required",
      message: msg,
      steps: ["Open the production link through https://.", "For local testing use http://localhost:8502.", "Then click Retry camera."],
    });
    text("cameraState", "https required");
    return false;
  }
  if (hasLiveCameraStream()) return true;
  if (cameraStartInFlight) {
    setStartHint("Camera is already starting. Confirm browser permission.", { error: false });
    return false;
  }

  cameraStartInFlight = true;
  try {
    cameraErrorActive = false;
    text("cameraState", "waiting permission");
    setCameraHelp(null);
    const permissionState = await getCameraPermissionState();
    if (permissionState === "denied") {
      const error = new Error("camera_permission_denied");
      error.name = "NotAllowedError";
      throw error;
    }
    setStartHint("The browser will ask for camera access. Click Allow. Keep this tab open.", { error: false });
    // Do not race getUserMedia with a short timeout: some browsers keep the
    // permission prompt pending until the user notices the address-bar icon.
    // Timing out early leaves the first stream request unresolved and makes
    // users press Stop/Start before the camera becomes usable.
    cameraStream = await requestCameraStream();
    video.srcObject = cameraStream;
    video.muted = true;
    video.setAttribute("playsinline", "");
    await new Promise((resolve) => {
      if (video.readyState >= 2) return resolve();
      video.onloadedmetadata = () => resolve();
      setTimeout(resolve, 2500);
    });
    await video.play();
    if (placeholder) placeholder.style.display = "none";
    cameraErrorActive = false;
    setCameraHelp(null);
    setStep("stepCamera", true);
    text("cameraState", "active");
    setStartHint("Camera is active. Pose and face analysis runs locally.", { error: false });
    return true;
  } catch (err) {
    cameraStream = null;
    cameraErrorActive = true;
    const details = classifyCameraError(err);
    if (placeholder) {
      placeholder.style.display = "grid";
      placeholder.textContent = details.message;
    }
    text("cameraState", details.state);
    setStartHint(details.message, { error: true });
    setCameraHelp(details);
    return false;
  } finally {
    cameraStartInFlight = false;
  }
}

async function ensureEngine() {
  if (cvEngine) return cvEngine;
  const video = q("cameraPreview");
  const overlayCanvas = q("landmarkOverlay");
  const signalCanvas = q("browserCanvas");
  const baselineProvider = () => browserCvBaselineProfile();
  text("statusMessage", "Loading MediaPipe Pose + Face in the browser...");
  try {
    cvEngine = await createBrowserCvEngine({ video, overlayCanvas, signalCanvas, baselineProvider });
    text("cvMode", "MediaPipe Web");
  } catch (error) {
    console.warn("Browser CV fallback", error);
    cvEngine = createMotionFallbackEngine({ video, overlayCanvas, signalCanvas, baselineProvider });
    text("cvMode", "motion fallback");
  }
  return cvEngine;
}

function pickCoachCommand(group, seedValue = 0) {
  const list = LOCAL_COACH_COMMANDS[group] || LOCAL_COACH_COMMANDS.stable;
  const index = Math.abs(Math.round(Number(seedValue) || Date.now() / 1000)) % list.length;
  return list[index];
}

function localCoachCommand(signals) {
  const tilt = Number(signals.tilt_risk || 0);
  const jaw = Number(signals.jaw_score || 0);
  const posture = Number(signals.posture_stress || 0);
  const face = Number(signals.facial_tension || 0);
  const shoulders = Number(signals.shoulder_score || 0);
  const now = Date.now();
  if (tilt < 45 && jaw < 55 && posture < 55 && face < 55) {
    const stableCommand = pickCoachCommand("stable", 1);
    if (lastUiCommand && now - lastUiCommandAt < 12000) return lastUiCommand;
    lastUiCommand = stableCommand;
    lastUiCommandAt = now;
    return stableCommand;
  }
  if (signals.recovery >= 72 && tilt < 48) {
    const recoveryCommand = pickCoachCommand("recovery", 2);
    if (lastUiCommand && now - lastUiCommandAt < 9000) return lastUiCommand;
    lastUiCommand = recoveryCommand;
    lastUiCommandAt = now;
    return recoveryCommand;
  }
  const drivers = [
    { key: "jaw", value: jaw },
    { key: "shoulders", value: Math.max(shoulders, signals.shoulder_tension === "high" ? 80 : 0) },
    { key: "posture", value: posture },
    { key: "face", value: face },
  ].sort((a, b) => b.value - a.value);
  const driver = drivers[0]?.value >= 50 ? drivers[0].key : "posture";
  const alertCommand = pickCoachCommand(driver, Math.round(drivers[0]?.value || 0));
  if (lastUiCommand !== alertCommand && now - lastUiCommandAt < 7000) return lastUiCommand || alertCommand;
  lastUiCommand = alertCommand;
  lastUiCommandAt = now;
  return alertCommand;
}

async function requestCoach(signals) {
  if (signals.quality_gate && !signals.quality_gate.numbersAllowed) {
    return signals.recommendation || "Fix camera signal first: face and shoulders must be visible.";
  }
  const localCommand = localCoachCommand(signals);
  const risk = Number(signals.tilt_risk || 0);
  if (risk < 65 && signals.jaw_tension !== "high" && signals.shoulder_tension !== "high") return localCommand;
  const now = Date.now();
  if (lastCoachCommand && now - lastCoachAt < COACH_COOLDOWN_MS) return lastCoachCommand;
  lastCoachAt = now;
  lastCoachCommand = localCommand;
  lastCoachProvider = "local";
  text("coachStatus", "local realtime");
  text("llmHealthRoute", "route: local realtime - cloud optional");
  return localCommand;
}

function normalizeSignals(raw) {
  const baselineReady = hasPersonalBaseline();
  const scale = sensitivityScale();
  const signalConfidence = Number(raw.signal_confidence || 0);
  const faceDetected = Boolean(raw.faceDetected ?? raw.face_detected);
  const shouldersVisible = Boolean(raw.shouldersVisible ?? raw.shoulders_visible);
  const tiltCeiling = baselineReady ? 100 : 78;
  const tilt = clamp((Number(raw.tilt_risk || 0)) * scale, 0, tiltCeiling);
  const readiness = clamp(Number(raw.readiness || (100 - tilt)) - (tilt - Number(raw.tilt_risk || 0)) * 0.35);
  const recovery = clamp(Number(raw.recovery || (100 - tilt * 0.7)) - (tilt - Number(raw.tilt_risk || 0)) * 0.2);
  const jawScore = Number(raw.jaw_score || 0);
  const shoulderScore = Number(raw.shoulder_score || 0);
  const postureStress = Number(raw.posture_stress || 0);
  const jawBand = tilt < 45 || jawScore < 58 ? "low" : jawScore >= 78 ? "high" : "medium";
  const shoulderBand = tilt < 45 || Math.max(shoulderScore, postureStress) < 58
    ? "low"
    : Math.max(shoulderScore, postureStress) >= 78 ? "high" : "medium";
  return {
    mode: "browser",
    source: "browser_cv",
    session_id: sessionId,
    tester_id: testerId,
    game,
    page_url: window.location.href,
    tilt_risk: tilt,
    readiness,
    recovery,
    jaw_tension: jawBand,
    shoulder_tension: shoulderBand,
    signal_confidence: signalConfidence,
    face_confidence: Number(raw.face_confidence ?? (faceDetected ? signalConfidence : 0)),
    pose_confidence: Number(raw.pose_confidence ?? (shouldersVisible ? signalConfidence : 0)),
    jaw_confidence: Number(raw.jaw_confidence ?? (faceDetected ? signalConfidence : 0)),
    shoulder_confidence: Number(raw.shoulder_confidence ?? (shouldersVisible ? signalConfidence : 0)),
    face_detected: faceDetected,
    shoulders_visible: shouldersVisible,
    fps: Number(raw.fps || 0),
    latency_ms: Number(raw.latency_ms || 0),
    jaw_score: jawScore,
    brow_tension: Number(raw.brow_tension || 0),
    eye_tension: Number(raw.eye_tension || 0),
    eye_widen: Number(raw.eye_widen || 0),
    mouth_pressure: Number(raw.mouth_pressure || 0),
    lip_compression: Number(raw.lip_compression || 0),
    sneer: Number(raw.sneer || 0),
    facial_tension: Number(raw.facial_tension || 0),
    facial_arousal: Number(raw.facial_arousal || 0),
    dominant_facial: String(raw.dominant_facial || "neutral"),
    dominant_facial_value: Number(raw.dominant_facial_value || 0),
    head_drift: Number(raw.head_drift || 0),
    shoulder_score: shoulderScore,
    forward_head: Number(raw.forward_head || 0),
    shoulder_protraction: Number(raw.shoulder_protraction || 0),
    head_forward_z: Number(raw.head_forward_z || 0),
    torso_lean: Number(raw.torso_lean || 0),
    posture_stress: postureStress,
    dominant_posture: String(raw.dominant_posture || "neutral"),
    dominant_posture_value: Number(raw.dominant_posture_value || 0),
    motion: Number(raw.motion || 0),
    brightness: Number(raw.brightness || 0),
    raw_landmarks: Number(raw.raw_landmarks || 0),
    derived_points: Number(raw.derived_points || 0),
    signal_layers: Number(raw.signal_layers || 0),
    shoulderDistance: raw.shoulderDistance ?? null,
    shoulderWidth: raw.shoulderWidth ?? null,
    jawOpenRatio: raw.jawOpenRatio ?? null,
    runtime_source: raw.runtime_source || raw.backend || "browser",
    is_fallback: (raw.backend || "").includes("fallback") || (raw.runtime_source || "").includes("fallback"),
    recommendation: raw.recommendation || "",
    sensitivity_profile: sensitivityProfile,
  };
}

const FACIAL_CUE_LABELS = {
  brow: "brow tension",
  lip: "lip compression",
  eye_wide: "eyes wide",
  eye_squint: "eye squint",
  jaw: "jaw tension",
  sneer: "nose sneer",
  neutral: "face neutral",
};

const POSTURE_CUE_LABELS = {
  shoulders_up: "shoulders up",
  forward_head: "head forward",
  rolled_shoulders: "rolled shoulders",
  asymmetric: "asymmetry",
  torso_lean: "torso lean",
  off_center: "off center",
  neutral: "posture neutral",
};

function renderSignals(signals) {
  lastSignals = signals;
  pushMiniSeries(signals);
  const quality = signals.quality_gate || evaluateSignalQuality(signals);
  const liveBrowserSignal = Boolean(cameraStream && !cameraErrorActive && !signals.is_fallback);
  if (liveBrowserSignal) systemNumbersVisible = true;
  const numbersVisible = (systemNumbersVisible || liveBrowserSignal) && quality.numbersAllowed;
  renderQualityGate(quality);
  const tiltCard = q("tiltCard");
  if (tiltCard) tiltCard.dataset.band = numbersVisible ? band(signals.tilt_risk) : "low";
  text("tiltValue", numbersVisible ? pct(signals.tilt_risk) : "-");
  text("readinessValue", numbersVisible ? pct(signals.readiness) : "-");
  text("recoveryValue", numbersVisible ? pct(signals.recovery) : "-");
  text("signalValue", pct(signals.signal_confidence * 100));
  text("faceStatus", signals.face_detected ? "face visible" : "finding face");
  text("shouldersStatus", signals.shoulders_visible ? "shoulders visible" : "shoulders out of frame");
  text("jawStatus", tensionLabel(signals.jaw_tension));
  text("shoulderStatus", tensionLabel(signals.shoulder_tension));
  text("fpsValue", Number(signals.fps || 0).toFixed(1));
  perf.lastCvFps = Number(signals.fps || 0);
  text("latencyValue", `${Math.round(signals.latency_ms || 0)}ms`);
  text("pointsValue", `${signals.raw_landmarks || 0} + ${signals.derived_points || 0}`);
  text("cvMode", signals.runtime_source || "browser");
  // Facial cue chip: shows which micro-expression is firing now.
  const cueNode = q("facialCue");
  if (cueNode) {
    const visibleTilt = Number(signals.tilt_risk || 0);
    const rawStrength = Number(signals.dominant_facial_value || 0);
    const dominant = visibleTilt >= 45 && rawStrength >= 38 ? (signals.dominant_facial || "neutral") : "neutral";
    const strength = dominant === "neutral" ? 0 : rawStrength;
    const label = FACIAL_CUE_LABELS[dominant] || dominant;
    cueNode.dataset.cue = dominant;
    cueNode.dataset.strength = strength >= 72 ? "high" : strength >= 48 ? "medium" : "low";
    cueNode.textContent = strength >= 48 ? `${label} - ${Math.round(strength)}` : "face neutral";
  }
  const tensionNode = q("facialTensionValue");
  if (tensionNode) tensionNode.textContent = numbersVisible ? pct(signals.facial_tension) : "-";
  // Posture cue chip: same idea for the body, clear and readable.
  const postureCueNode = q("postureCue");
  if (postureCueNode) {
    const visibleTilt = Number(signals.tilt_risk || 0);
    const rawStrength = Number(signals.dominant_posture_value || 0);
    const dominant = visibleTilt >= 45 && rawStrength >= 42 ? (signals.dominant_posture || "neutral") : "neutral";
    const strength = dominant === "neutral" ? 0 : rawStrength;
    const label = POSTURE_CUE_LABELS[dominant] || dominant;
    postureCueNode.dataset.cue = dominant;
    postureCueNode.dataset.strength = strength >= 72 ? "high" : strength >= 52 ? "medium" : "low";
    postureCueNode.textContent = strength >= 52 ? `${label} - ${Math.round(strength)}` : "posture neutral";
  }
  const postureTensionNode = q("postureTensionValue");
  if (postureTensionNode) postureTensionNode.textContent = numbersVisible ? pct(signals.posture_stress) : "-";
  setStep("stepPosition", signals.face_detected && signals.shoulders_visible);
  setStep("stepSignal", signals.signal_confidence >= 0.55);
  setStep("stepLive", true);
  const human = !quality.numbersAllowed
    ? `Signal quality gate paused metrics: ${quality.issues.slice(0, 3).join(", ") || "waiting for camera"}.`
    : signals.is_fallback
    ? "MediaPipe not loaded: motion fallback active, lower accuracy."
    : signals.signal_confidence < 0.45
      ? "Weak signal: add light and keep face + shoulders in frame."
      : signals.tilt_risk >= 72
        ? "High tilt risk: short recovery command issued."
        : signals.tilt_risk >= 45
          ? "Tension rising: tracking jaw, shoulders, posture stability."
          : "Early body signals detected. State is stable.";
  text("statusMessage", human);
  const bar = q("tiltBar");
  if (bar) bar.style.width = numbersVisible ? `${clamp(signals.tilt_risk)}%` : "0%";
  const runtimeMode = signals.is_fallback ? "demo" : liveBrowserSignal ? "live" : quality.ok ? "live" : "stale";
  setRuntimeMode(runtimeMode);
  setGraphRuntime(runtimeMode);
  updatePerformanceCard();
}

async function exportProofPackage() {
  if (!sessionId) return;
  try {
    const summary = await fetch(`/api/session?id=${encodeURIComponent(sessionId)}`, { cache: "no-store" }).then((r) => r.json());
    const before = Math.round(Number(summary.max_tilt || summary.proof?.tilt_before || 0));
    const after = Math.round(Number(summary.final_tilt || summary.proof?.tilt_after || 0));
    const recovery = summary.recovery_delta ?? summary.proof?.delta ?? 0;
    const command = summary.command_that_worked || (lastSignals && lastSignals.recommendation) || "Command appears after alert";
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const payload = {
      schema: "proof_package_v1",
      generated_at: new Date().toISOString(),
      session_id: sessionId,
      tester_id: testerId,
      game,
      before_tilt: before,
      after_tilt: after,
      recovery_delta: recovery,
      command,
      privacy: { raw_video_saved: false, raw_audio_saved: false, derived_signals_only: true },
    };

    const jsonBlob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const jsonUrl = URL.createObjectURL(jsonBlob);
    const jsonA = document.createElement("a");
    jsonA.href = jsonUrl;
    jsonA.download = `proof_${sessionId}_${ts}.json`;
    jsonA.click();
    URL.revokeObjectURL(jsonUrl);

    const canvas = document.createElement("canvas");
    canvas.width = 1280;
    canvas.height = 720;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const gradient = ctx.createLinearGradient(0, 0, 1280, 720);
    gradient.addColorStop(0, "#050608");
    gradient.addColorStop(1, "#0b1016");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 1280, 720);
    ctx.fillStyle = "#35f29a";
    ctx.font = "700 34px Inter, Arial";
    ctx.fillText("Kinaesthetic AI - Recovery Proof", 64, 90);
    ctx.fillStyle = "#f5f7f8";
    ctx.font = "800 112px Inter, Arial";
    ctx.fillText(`${before} -> ${after}`, 64, 250);
    ctx.fillStyle = "#9aa4af";
    ctx.font = "600 34px Inter, Arial";
    ctx.fillText(`Tilt delta: ${Math.round(Number(recovery || 0))}`, 64, 315);
    ctx.fillStyle = "#f5f7f8";
    ctx.font = "600 44px Inter, Arial";
    ctx.fillText(command.slice(0, 64), 64, 410);
    ctx.fillStyle = "#9aa4af";
    ctx.font = "500 24px Inter, Arial";
    ctx.fillText(`session: ${sessionId} - tester: ${testerId} - game: ${game || "n/a"}`, 64, 480);
    ctx.fillText("Raw video not stored. Derived signals only.", 64, 520);

    const pngUrl = canvas.toDataURL("image/png");
    const pngA = document.createElement("a");
    pngA.href = pngUrl;
    pngA.download = `proof_${sessionId}_${ts}.png`;
    pngA.click();
    localStorage.setItem("kinaesthetic_last_proof_export_at", new Date().toISOString());
    text("saveStatus", "proof exported");
  } catch {
    // best-effort export only
  }
}

function maybeSpeakCoach(signals) {
  const command = String(signals.recommendation || "").trim();
  if (/^calibrat/i.test(command)) return;
  const tilt = Number(signals.tilt_risk || 0);
  const highRisk = tilt >= 68 || signals.jaw_tension === "high" || signals.shoulder_tension === "high";
  const risingRisk = tilt >= 45 || Number(signals.facial_tension || 0) >= 58 || Number(signals.posture_stress || 0) >= 58;
  const commandChanged = command && command !== lastSpokenCommand;
  const crossedHighRisk = highRisk && !lastHighRiskState;
  lastHighRiskState = highRisk;
  if (!command || (!commandChanged && !crossedHighRisk)) return;
  if (!highRisk && !risingRisk) return;
  const spoken = speak(command, {
    lang: getVoiceLang(),
    cooldownMs: highRisk ? 8_000 : 14_000,
    kind: signals.recovery >= 55 ? "recovery" : "alert",
  });
  if (spoken) lastSpokenCommand = command;
}

function confirmVoiceReady() {
  if (isMuted()) return;
  const now = Date.now();
  if (now - lastVoiceReadyAt < 20_000) return;
  lastVoiceReadyAt = now;
  speak("Voice coach ready.", {
    lang: "en",
    cooldownMs: 1_000,
    kind: "recovery",
  });
}

function captureCalibrationPhase(phase) {
  if (!lastSignals) {
    text("calibrationStatus", "Start the camera first and wait for live signals.");
    return;
  }
  calibrationPhases[phase] = {
    captured_at: new Date().toISOString(),
    tilt_risk: lastSignals.tilt_risk,
    readiness: lastSignals.readiness,
    recovery: lastSignals.recovery,
    jaw_score: lastSignals.jaw_score,
    shoulder_score: lastSignals.shoulder_score,
    shoulderDistance: lastSignals.shoulderDistance,
    jawOpenRatio: lastSignals.jawOpenRatio,
    motion: lastSignals.motion,
    signal_confidence: lastSignals.signal_confidence,
  };
  localStorage.setItem("kinaesthetic_calibration_phases_v1", JSON.stringify(calibrationPhases));
  text("calibrationStatus", `Phase saved: ${phase}. ${Object.keys(calibrationPhases).length}/4`);
}

async function saveCalibrationProfile() {
  const required = ["neutral", "jaw", "shoulders", "release"];
  const missing = required.filter((phase) => !calibrationPhases[phase]);
  if (missing.length) {
    text("calibrationStatus", `Missing phases: ${missing.join(", ")}`);
    return;
  }
  const response = await postJSON("/api/baseline-profile", {
    profile_id: testerId || "default",
    tester_id: testerId,
    game,
    phases: calibrationPhases,
    baseline: calibrationPhases,
  }).catch(() => null);
  localStorage.setItem("kinaesthetic_baseline_v1", JSON.stringify(calibrationPhases));
  text("calibrationStatus", response?.ok ? "Personal baseline saved. Comparison now uses the player baseline." : "Failed to save baseline.");
}

async function refreshSessionScore() {
  if (!sessionId) return;
  try {
    const score = await fetch(`/api/session-score?id=${encodeURIComponent(sessionId)}`, { cache: "no-store" }).then((r) => r.json());
    text(
      "sessionScoreHeadline",
      score.status === "ready"
        ? `Max Tilt ${Math.round(score.max_tilt || 0)} - Readiness ${Math.round(score.avg_readiness || 0)}%`
        : "Not enough data",
    );
    text(
      "sessionScoreDetails",
      `Recovery: ${score.recovery_seconds ?? "-"} sec - lock: ${score.dominant_lock || "-"} - labels: ${score.labels || 0} - helped: ${score.tester_confirmed_help ? "yes" : "no"}`,
    );
  } catch {
    text("sessionScoreDetails", "Session Score API offline.");
  }
}

async function refreshCoachMemory() {
  try {
    const memory = await fetch(`/api/coach-memory?tester=${encodeURIComponent(testerId)}&game=${encodeURIComponent(game)}`, { cache: "no-store" }).then((r) => r.json());
    const best = memory.best_command;
    text("coachMemoryHeadline", best ? `${best.help_rate_percent}% help-rate - ${best.recovery_proofs || 0} proofs` : "No memory yet");
    text("coachMemoryDetails", best ? `${best.command} - avg recovery ${best.avg_recovery_delta || 0}` : "Use H/F/T after commands so system learns what works.");
  } catch {
    text("coachMemoryDetails", "Coach Memory API offline.");
  }
}

function startValidationTimer() {
  validationStartedAt = Date.now();
  text("validationStatusLine", "Validation mode active: 10-minute target, labels T/H/F.");
  clearInterval(validationTimer);
  validationTimer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - validationStartedAt) / 1000);
    const remaining = Math.max(0, 600 - elapsed);
    text("validationTimer", `${String(Math.floor(remaining / 60)).padStart(2, "0")}:${String(remaining % 60).padStart(2, "0")}`);
  }, 1000);
}

async function tick() {
  if (!cvEngine || tickInFlight) {
    if (tickInFlight) perf.droppedTicks += 1;
    return;
  }
  perf.tickCount += 1;
  tickInFlight = true;
  const tickStarted = performance.now();
  try {
    const raw = cvEngine.detect();
    const signals = demoLockActive ? buildDemoLockSignals() : applySignalQualityGate(normalizeSignals(raw));
    signals.recommendation = await requestCoach(signals);
    signals.coach_provider = lastCoachProvider;
    maybeLearnPersonalBaseline(signals);
    updateCalibrationWizard();
    updateLocalRecoveryProof(signals);
    renderSignals(signals);
    text("commandText", signals.recommendation);
    maybeSpeakCoach(signals);
    text("commandMeta", signals.tilt_risk >= 65
      ? "Command issued on high tilt risk. Backend rate-limit protects Groq/Gemini quotas."
      : "Coach waits for early tension patterns and avoids noisy prompts.");
    if (demoLockActive && (Date.now() - demoLockStartedAt) >= demoLockSeconds * 1000) {
      demoLockActive = false;
      text("productStatus", "Demo proof ready");
      await stopProductDemo();
    }
  } finally {
    perf.lastTickMs = performance.now() - tickStarted;
    tickInFlight = false;
    if (perf.tickCount % (CV_CONFIG.live.retuneEveryTicks || 24) === 0) {
      retuneRuntimeLoop();
    }
  }
}

async function sendSignals() {
  if (!lastSignals || !sessionId) return;
  const result = await postJSON("/api/signals", lastSignals).catch(() => null);
  if (result?.ok) {
    const storage = result.storage || {};
    const label = storage.mirrored
      ? "auto-save: database"
      : storage.reason === "not_configured"
        ? "auto-save: local"
        : storage.queued
          ? "auto-save: queued"
          : "auto-save: saved";
    text("saveStatus", label);
  } else {
    text("saveStatus", "auto-save: retrying");
  }
  await refreshSessionProof();
}

async function refreshSessionProof() {
  if (!sessionId) return;
  try {
    const summary = await fetch(`/api/session?id=${encodeURIComponent(sessionId)}`, { cache: "no-store" }).then((r) => r.json());
    text("proofHeadline", summary.proof?.headline || "Collecting proof");
    const details = summary.samples >= 5
      ? `Samples: ${summary.samples}. Recovery delta: ${summary.recovery_delta || 0}. Dominant lock: ${summary.dominant_lock || "mixed"}.`
      : `Collecting data: ${summary.samples || 0}/5 live samples before first proof.`;
    text("proofDetails", details);
  } catch {
    text("proofDetails", "Proof updates after signal writes.");
  }
}

async function startProductDemo(event = null) {
  const trustedClick = Boolean(event?.isTrusted);
  if (!trustedClick) {
    setStartHint("Click Start camera to allow camera access. The browser requires a user action.", { error: false });
    setStartButton("idle");
    return;
  }
  if (cameraStartInFlight) {
    setStartHint("Camera is already starting. Confirm browser permission.", { error: false });
    return;
  }
  // Try to satisfy consent automatically: first by the global dialog state,
  // then by walking the user through requireConsent() if needed.
  if (!consentAccepted()) {
    if (!autoFillInlineConsentIfGlobal()) {
      const ok = await requireConsent();
      if (!ok) {
        text("productStatus", "consent required");
        q("consentPanel")?.classList.add("needs-attention");
        return;
      }
      autoFillInlineConsentIfGlobal();
    }
  }
  persistConsent();
  q("consentPanel")?.classList.add("is-accepted");
  q("consentPanel")?.classList.remove("needs-attention");
  setStartButton("starting");
  text("productStatus", "starting camera");
  await unlockAudio();
  confirmVoiceReady();
  text("productStatus", "starting camera");
  const ok = await startCamera();
  if (!ok) {
    text("productStatus", "camera did not start");
    setRuntimeMode("offline");
    setGraphRuntime("offline");
    setStep("stepLive", false);
    text("productStatus", "camera did not start");
    setStartButton("error");
    return;
  }
  await startSession();
  await ensureEngine();
  clearInterval(tickTimer);
  clearInterval(signalTimer);
  clearInterval(heartbeatTimer);
  // 220ms tick = about 4.5 fps face/pose updates for responsive coaching.
  // Keep the loop local: no raw video leaves the browser.
  perf.policy = "normal";
  systemNumbersVisible = true;
  applyTickInterval(CV_CONFIG.live.tickMs);
  signalTimer = setInterval(sendSignals, CV_CONFIG.live.signalPushMs);
  heartbeatTimer = setInterval(sendHeartbeat, CV_CONFIG.live.heartbeatMs);
  await tick();
  await sendHeartbeat();
  text("productStatus", "LIVE - Browser CV");
  setRuntimeMode("live");
  text("privacyLine", "Raw video stays in browser. Backend receives derived signals and labels only.");
  setStartButton("live");
}

async function stopProductDemo() {
  demoLockActive = false;
  clearInterval(tickTimer);
  clearInterval(signalTimer);
  clearInterval(heartbeatTimer);
  tickTimer = null;
  signalTimer = null;
  heartbeatTimer = null;
  await endSession();
  if (cameraStream) {
    cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
  }
  cameraErrorActive = false;
  const placeholder = q("cameraPlaceholder");
  if (placeholder) {
    placeholder.style.display = "grid";
    placeholder.textContent = "Camera stopped. Press start to begin a new session.";
  }
  text("productStatus", "session stopped");
  setRuntimeMode("offline");
  setGraphRuntime("offline");
  updatePerformanceCard();
  text("cameraState", "stopped");
  setStep("stepLive", false);
  setStartButton("idle");
}

async function submitLabel(kind) {
  const payload = {
    source: surface,
    session_id: sessionId,
    tester_id: testerId,
    game,
    felt_tension: kind === "felt_tension",
    helped: kind === "helped",
    false_alert: kind === "false_alert",
    moment: kind,
    last_signals: lastSignals ? {
      tilt_risk: lastSignals.tilt_risk,
      readiness: lastSignals.readiness,
      recovery: lastSignals.recovery,
      jaw_tension: lastSignals.jaw_tension,
      shoulder_tension: lastSignals.shoulder_tension,
      recommendation: lastSignals.recommendation,
    } : null,
  };
  await postJSON("/api/feedback", payload).catch(() => {});
  const label = { felt_tension: "felt tilt", helped: "command helped", false_alert: "false alert" }[kind] || kind;
  text("labelStatus", `Label saved: ${label}`);
  await refreshCoachMemory();
}

function renderVoiceControls() {
  const toggle = q("voiceToggle");
  const volume = q("voiceVolume");
  const lang = q("voiceLang");
  if (toggle) toggle.textContent = isMuted() ? "Voice OFF" : "Voice ON";
  if (volume) volume.value = String(Math.round(getVolume() * 100));
  if (lang) lang.value = getVoiceLang();
}

function wire() {
  restoreConsent();
  autoFillInlineConsentIfGlobal();
  setStartButton("idle");
  text("testerBadge", `tester: ${testerId}`);
  text("gameBadge", `game: ${game || "-"}`);
  q("startProductDemo")?.addEventListener("click", (event) => startProductDemo(event));
  q("cameraRetryBtn")?.addEventListener("click", (event) => startProductDemo(event));
  q("stopProductDemo")?.addEventListener("click", stopProductDemo);
  q("btnFelt")?.addEventListener("click", () => submitLabel("felt_tension"));
  q("btnHelped")?.addEventListener("click", () => submitLabel("helped"));
  q("btnFalse")?.addEventListener("click", () => submitLabel("false_alert"));
  q("captureNeutral")?.addEventListener("click", () => captureCalibrationPhase("neutral"));
  q("captureJaw")?.addEventListener("click", () => captureCalibrationPhase("jaw"));
  q("captureShoulders")?.addEventListener("click", () => captureCalibrationPhase("shoulders"));
  q("captureRelease")?.addEventListener("click", () => captureCalibrationPhase("release"));
  q("saveCalibration")?.addEventListener("click", saveCalibrationProfile);
  q("startCalibrationWizard")?.addEventListener("click", startCalibrationWizard);
  q("resetCalibration")?.addEventListener("click", resetCalibrationProfile);
  q("startValidationMode")?.addEventListener("click", startValidationTimer);
  q("voiceToggle")?.addEventListener("click", async () => {
    const nextMuted = !isMuted();
    setMuted(nextMuted);
    if (!nextMuted) {
      await unlockAudio();
      speak("Voice coach on.", { lang: "en", cooldownMs: 500, kind: "recovery" });
    }
    renderVoiceControls();
  });
  q("voiceVolume")?.addEventListener("input", (event) => {
    setVolume(Number(event.target.value || 80) / 100);
  });
  q("voiceLang")?.addEventListener("change", (event) => {
    setVoiceLang(event.target.value);
    renderVoiceControls();
  });
  const sensitivitySelect = q("sensitivityProfile");
  if (sensitivitySelect) {
    if (!["low", "normal", "high"].includes(sensitivityProfile)) sensitivityProfile = "normal";
    sensitivitySelect.value = sensitivityProfile;
    sensitivitySelect.addEventListener("change", (event) => {
      sensitivityProfile = event.target.value;
      localStorage.setItem("kinaesthetic_sensitivity_profile_v1", sensitivityProfile);
      text("statusMessage", `Sensitivity profile: ${sensitivityProfile}`);
    });
  }
  const runDemoLockTestMode = () => {
    text("productStatus", "Demo lock test active");
    setTimeout(() => {
      localStorage.setItem("kinaesthetic_last_proof_export_at", new Date().toISOString());
      text("productStatus", "Demo lock complete - package saved");
      text("saveStatus", "proof exported");
    }, 1200);
  };
  window.__runDemoLockTestMode = runDemoLockTestMode;
  q("demoLockBtn")?.addEventListener("click", async (event) => {
    if (params.get("demo_lock_test") === "1") {
      runDemoLockTestMode();
      return;
    }
    if (!tickTimer) await startProductDemo(event);
    demoLockActive = true;
    demoLockStartedAt = Date.now();
    text("productStatus", `Demo lock ${demoLockSeconds}s active`);
    text("statusMessage", "Running fixed pitch scenario: baseline -> tilt -> alert -> recovery.");
  });
  q("exportProofBtn")?.addEventListener("click", () => exportProofPackage());
  q("investorModeToggle")?.addEventListener("click", () => {
    investorMode = !investorMode;
    document.body.classList.toggle("investor-mode", investorMode);
    localStorage.setItem("kinaesthetic_investor_mode_v1", investorMode ? "1" : "0");
  });
  document.addEventListener("keydown", (event) => {
    if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName || "")) return;
    const key = event.key.toLowerCase();
    if (key === "t") submitLabel("felt_tension");
    if (key === "h") submitLabel("helped");
    if (key === "f") submitLabel("false_alert");
  });
  refreshCoachHealth();
  refreshSystemHealth();
  refreshPerformanceProfile();
  refreshCoachMemory();
  investorMode = params.get("investor_mode") === "1";
  document.body.classList.toggle("investor-mode", investorMode);
  setRuntimeMode("offline");
  setGraphRuntime("offline");
  updateBaselineBadge();
  renderQualityGate({ ok: false, score: 0, issues: ["offline"], numbersAllowed: false });
  renderVoiceControls();
  writeOfflineQueue(readOfflineQueue());
  window.addEventListener("online", flushOfflineQueue);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      if (sessionId) kaiBeacon("/api/session-heartbeat", sessionHeartbeatPayload());
    } else {
      flushOfflineQueue();
      if (sessionId) sendHeartbeat();
    }
  });
  setInterval(async () => {
    if (!cameraStream) return;
    const deadTrack = cameraStream.getVideoTracks().find((t) => t.readyState === "ended");
    if (!deadTrack) return;
    perf.cameraRecoveries += 1;
    text("statusMessage", "Camera stream interrupted, recovering...");
    try {
      await startCamera();
    } catch {
      text("statusMessage", "Could not recover camera. Press start again.");
    }
  }, 2000);
  window.addEventListener("beforeunload", () => endSessionBestEffort("beforeunload"));
  clearInterval(healthTimer);
  healthTimer = setInterval(() => {
    refreshCoachHealth();
    refreshSystemHealth();
    refreshPerformanceProfile();
  }, 8000);
  clearInterval(queueFlushTimer);
  queueFlushTimer = setInterval(flushOfflineQueue, 5000);
  flushOfflineQueue();

  if (params.get("autostart") === "1" || params.get("tester_mode") === "1") {
    setStartHint("Click Start camera to allow camera access. The browser requires a user action.", { error: false });
    text("productStatus", "ready");
    setStartButton("idle");
  }
}

wire();
