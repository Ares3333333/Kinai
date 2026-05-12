import { kaiPost } from "/kai_api.js";

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
};

const overlayParams = new URLSearchParams(window.location.search);
const overlayMode = overlayParams.get("mode") || "streamer";
document.body.dataset.hudMode = overlayMode;

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
  if (state.mode === "offline") return "Live engine не подключён";
  if (state.mode === "stale") return "Live engine не обновляется";
  if (state.mode === "demo") return "Демо-режим";
  if (state.alert_level === "low_signal") return "Улучшите свет / сядьте перед камерой";
  if (state.alert_level === "recovery") return state.recommendation || "Сохраняй мягкость";
  if (state.alert_level === "warning") {
    return state.recommendation || "Расслабь челюсть · опусти плечи · 20 секунд на сброс";
  }
  return state.recommendation || `Готовность ${pct(state.readiness)}`;
}

function render(state) {
  const isOffline = state.mode === "offline" || state.mode === "stale";
  const hideMetrics = isOffline || state.numbers_visible === false;
  document.body.dataset.alert = isOffline ? "offline" : state.alert_level || "normal";
  document.body.dataset.mode = state.mode || "offline";
  nodes.statusText.textContent = statusCopy(state);
  nodes.tiltRisk.textContent = hideMetrics ? "--" : pct(state.tilt_risk);
  nodes.tiltBar.style.width = hideMetrics ? "0%" : pct(state.tilt_risk);
  nodes.readiness.textContent = hideMetrics ? "--" : pct(state.readiness);
  nodes.recovery.textContent = hideMetrics ? "--" : pct(state.recovery);
  nodes.jawTension.textContent = hideMetrics ? "--" : tension[state.jaw_tension] || state.jaw_tension || "--";
  nodes.shoulderTension.textContent = hideMetrics ? "--" : tension[state.shoulder_tension] || state.shoulder_tension || "--";
  nodes.recommendation.textContent = recommendationCopy(state);
  nodes.confidence.textContent = pct((Number(state.signal_confidence ?? state.confidence) || 0) * 100);
  nodes.sourceBadge.textContent = state.mode === "live"
    ? "LIVE"
    : state.mode === "demo"
      ? "DEMO"
      : state.mode === "stale"
        ? "STALE"
        : "OFFLINE";
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
    const merged = {
      ...state,
      mode: String(state.mode || health?.mode || "offline").toLowerCase(),
      numbers_visible: state.numbers_visible ?? health?.numbers_visible ?? true,
    };
    render(merged);
  } catch {
    // OBS should stay visually stable if the API briefly disappears.
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
    // Keep overlay quiet during stream capture.
  }
}

window.addEventListener("keydown", (event) => {
  saveOverlayLabel(event.key.toLowerCase());
});

refresh();
setInterval(refresh, 400);
