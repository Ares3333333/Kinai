import { kaiPost } from "/kai_api.js";
import { isMuted, setMuted, speak, unlockAudio } from "/tts_player.js";

const nodes = {
  statusText: document.querySelector("#statusText"),
  tiltRisk: document.querySelector("#tiltRisk"),
  tiltBar: document.querySelector("#tiltBar"),
  readiness: document.querySelector("#readiness"),
  recovery: document.querySelector("#recovery"),
  jawTension: document.querySelector("#jawTension"),
  shoulderTension: document.querySelector("#shoulderTension"),
  recommendation: document.querySelector("#recommendation"),
  confidence: document.querySelector("#confidence"),
  sourceBadge: document.querySelector("#sourceBadge"),
  labelFlash: document.querySelector("#labelFlash"),
  voiceUnlock: document.querySelector("#voiceUnlock"),
};

const overlayParams = new URLSearchParams(window.location.search);
const overlayMode = overlayParams.get("mode") || "streamer";
const voiceEnabled = overlayParams.get("voice") !== "0";
document.body.dataset.hudMode = overlayMode;
document.body.dataset.windowOverlay = overlayParams.get("window") === "1" ? "true" : "false";

let voiceUnlocked = false;
let lastVoiceCommand = "";
let lastVoiceAt = 0;

const tension = {
  low: "низкое",
  medium: "среднее",
  high: "высокое",
};

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${Math.round(Math.max(0, Math.min(100, Number(value) || 0)))}%`;
}

function statusCopy(state) {
  if (state.mode === "live") return "LIVE: идёт анализ";
  if (state.mode === "demo") return "DEMO: сценарий для записи";
  if (state.mode === "stale") return "STALE: сигнал устарел";
  if (state.mode === "offline") return "OFFLINE: анализ не идёт";
  if (state.alert_level === "low_signal") return "Слабый сигнал";
  if (state.alert_level === "recovery") return "Восстановление улучшается";
  if (state.alert_level === "warning") return "Риск тильта растёт";
  return "Готов";
}

function recommendationCopy(state) {
  if (state.mode === "offline") return "Открой /play и нажми Старт с камерой";
  if (state.mode === "stale") return "Сигнал устарел. Проверь /play";
  if (state.mode === "demo") return "Демо-режим";
  if (state.alert_level === "low_signal") return "Улучши свет и сядь перед камерой";
  if (state.alert_level === "recovery") return state.recommendation || "Сохраняй мягкость";
  if (state.alert_level === "warning") {
    return state.recommendation || "Челюсть мягко. Плечи вниз. Длинный выдох.";
  }
  return state.recommendation || `Готовность ${pct(state.readiness)}`;
}

function maybeSpeak(state, command) {
  if (!voiceEnabled || !voiceUnlocked || isMuted()) return;
  const shouldSpeak = state.mode === "live" && ["warning", "recovery"].includes(state.alert_level);
  if (!shouldSpeak || !command || command === lastVoiceCommand) return;
  const now = Date.now();
  if (now - lastVoiceAt < 9000) return;
  lastVoiceCommand = command;
  lastVoiceAt = now;
  speak(command, { cooldownMs: 9000, kind: state.alert_level === "recovery" ? "recovery" : "alert" });
}

function render(state) {
  const isOffline = state.mode === "offline" || state.mode === "stale";
  const hideMetrics = isOffline || state.numbers_visible === false;
  const command = recommendationCopy(state);
  document.body.dataset.alert = isOffline ? "offline" : state.alert_level || "normal";
  document.body.dataset.mode = state.mode || "offline";
  nodes.statusText.textContent = statusCopy(state);
  nodes.tiltRisk.textContent = hideMetrics ? "--" : pct(state.tilt_risk);
  nodes.tiltBar.style.width = hideMetrics ? "0%" : pct(state.tilt_risk);
  nodes.readiness.textContent = hideMetrics ? "--" : pct(state.readiness);
  nodes.recovery.textContent = hideMetrics ? "--" : pct(state.recovery);
  nodes.jawTension.textContent = hideMetrics ? "--" : tension[state.jaw_tension] || state.jaw_tension || "--";
  nodes.shoulderTension.textContent = hideMetrics ? "--" : tension[state.shoulder_tension] || state.shoulder_tension || "--";
  nodes.recommendation.textContent = command;
  nodes.confidence.textContent = pct((Number(state.signal_confidence ?? state.confidence) || 0) * 100);
  nodes.sourceBadge.textContent = state.mode === "live"
    ? "LIVE"
    : state.mode === "demo"
      ? "DEMO"
      : state.mode === "stale"
        ? "STALE"
        : "OFFLINE";
  maybeSpeak(state, command);
}

async function refresh() {
  try {
    const [stateResponse, healthResponse] = await Promise.all([
      fetch("/api/state", { cache: "no-store" }),
      fetch("/api/system-health", { cache: "no-store" }),
    ]);
    if (!stateResponse.ok) return;
    const state = await stateResponse.json();
    const health = healthResponse.ok ? await healthResponse.json() : null;
    render({
      ...state,
      mode: String(state.mode || health?.mode || "offline").toLowerCase(),
      numbers_visible: state.numbers_visible ?? health?.numbers_visible ?? true,
    });
  } catch {
    // Keep the overlay visually stable while the site server restarts.
  }
}

async function saveOverlayLabel(key) {
  const map = { t: "felt_tension", h: "helped", f: "false_alert" };
  const label = map[key];
  if (!label) return;
  try {
    const result = await kaiPost("/api/feedback", {
      source: "overlay_hotkey",
      moment: `overlay_${key}`,
      felt_tension: label === "felt_tension",
      helped: label === "helped",
      false_alert: label === "false_alert",
    });
    if (result?.ok) {
      nodes.labelFlash.hidden = false;
      nodes.labelFlash.textContent = `Label ${key.toUpperCase()} saved`;
      setTimeout(() => { nodes.labelFlash.hidden = true; }, 1200);
    }
  } catch {
    // Keep overlay quiet during recording.
  }
}

async function enableVoice() {
  setMuted(false);
  voiceUnlocked = await unlockAudio();
  nodes.voiceUnlock.textContent = voiceUnlocked ? "Голос включён" : "Голос недоступен";
  nodes.voiceUnlock.dataset.ready = voiceUnlocked ? "true" : "false";
  setTimeout(() => {
    if (voiceUnlocked) nodes.voiceUnlock.hidden = true;
  }, 1600);
}

nodes.voiceUnlock?.addEventListener("click", enableVoice);
window.addEventListener("pointerdown", () => {
  if (!voiceUnlocked && voiceEnabled) enableVoice();
}, { once: true });
window.addEventListener("keydown", (event) => {
  if (event.key.toLowerCase() === "m") {
    setMuted(!isMuted());
    nodes.voiceUnlock.hidden = false;
    nodes.voiceUnlock.textContent = isMuted() ? "Голос выключен" : "Голос включён";
    return;
  }
  saveOverlayLabel(event.key.toLowerCase());
});

refresh();
setInterval(refresh, 400);
