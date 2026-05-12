// /play page logic — single-screen tester surface.
// Wires up: state polling, in-context feedback, hotkeys, voice mute,
// shareable proof export, language toggle, first-run consent.

import { applyTranslations, currentLang, t, toggleLang } from "/i18n.js";
import { requireConsent } from "/consent.js";
import { isMuted, pickPhrase, speak, toggleMute } from "/tts_player.js";
import { saveProofPng } from "/share_proof.js";
import { kaiPost } from "/kai_api.js";

const POLL_INTERVAL_MS = 800;
const STATS_INTERVAL_MS = 4000;
const ALERT_LEVELS_TO_KEY = { warning: "reset", recovery: "breath", normal: "default" };
const LABEL_GOAL = 150;
const BASELINE_KEY = "kinaesthetic_play_baseline_v2";

const dom = {
  stage: document.getElementById("playStage"),
  tiltValue: document.getElementById("playTiltValue"),
  tiltSuffix: document.getElementById("playTiltSuffix"),
  tiltBand: document.getElementById("playTiltBand"),
  tiltBar: document.getElementById("playTiltBar"),
  tiltStatus: document.getElementById("playTiltStatus"),
  commandText: document.getElementById("playCommandText"),
  commandMeta: document.getElementById("playCommandMeta"),
  feedback: document.getElementById("playFeedback"),
  feedbackStatus: document.getElementById("playFeedbackStatus"),
  fps: document.getElementById("playFps"),
  confidence: document.getElementById("playConfidence"),
  mode: document.getElementById("playMode"),
  labels: document.getElementById("playLabels"),
  validationProgress: document.getElementById("playValidationProgress"),
  validationBar: document.getElementById("playValidationBar"),
  helpRate: document.getElementById("playHelpRate"),
  falseRate: document.getElementById("playFalseRate"),
  recentLabels: document.getElementById("playRecentLabels"),
  baselineStatus: document.getElementById("playBaselineStatus"),
  baselineDrift: document.getElementById("playBaselineDrift"),
  personalScore: document.getElementById("playPersonalScore"),
  personalMessage: document.getElementById("playPersonalMessage"),
  baselineButtons: document.querySelectorAll("[data-baseline]"),
  autopilotEta: document.getElementById("playAutopilotEta"),
  autopilotText: document.getElementById("playAutopilotText"),
  autopilotSignal: document.getElementById("playAutopilotSignal"),
  recoveryDelta: document.getElementById("playRecoveryDelta"),
  recoveryCommand: document.getElementById("playRecoveryCommand"),
  recoverySeconds: document.getElementById("playRecoverySeconds"),
  recoverySource: document.getElementById("playRecoverySource"),
  demoReady: document.getElementById("playDemoReady"),
  frameMs: document.getElementById("playFrameMs"),
  cvMs: document.getElementById("playCvMs"),
  coachHealth: document.getElementById("playCoachHealth"),
  shareBtn: document.getElementById("playShareProof"),
  muteToggle: document.getElementById("muteToggle"),
  reliabilityToggle: document.getElementById("reliabilityToggle"),
  langToggle: document.getElementById("langToggle"),
  engineBanner: document.getElementById("engineBanner"),
};

let lastSpokenAlert = "";
let lastAlertLevel = "normal";
let lastConfidence = 0;
let lastState = null;
let playBaseline = JSON.parse(localStorage.getItem(BASELINE_KEY) || "null");
let reliabilityMode = localStorage.getItem("kinaesthetic_reliability_mode") === "1";

// Map raw mode strings to a friendly label users actually understand.
function modeLabel(mode) {
  if (mode === "live") return t("play.mode_live") || "live";
  if (mode === "stale") return t("play.mode_stale") || "stale";
  if (mode === "demo") return t("play.mode_demo") || "demo";
  return t("play.mode_offline") || "offline";
}

function setMuteLabel() {
  const labelKey = isMuted() ? "play.mute_off" : "play.mute_on";
  dom.muteToggle.querySelector("span").textContent = t(labelKey);
}

function tiltLabel(score) {
  if (!Number.isFinite(score)) return "--";
  if (score >= 70) return t("play.tilt_high");
  if (score >= 40) return t("play.tilt_medium");
  return t("play.tilt_low");
}

function setMode(mode) {
  dom.stage.dataset.mode = mode || "offline";
  if (dom.mode) dom.mode.textContent = modeLabel(mode);
}

function setReliabilityLabel() {
  document.body.dataset.reliabilityMode = reliabilityMode ? "on" : "off";
  const span = dom.reliabilityToggle?.querySelector("span");
  if (span) span.textContent = reliabilityMode ? "Investor mode on" : "Показ инвестору";
}

function percent(value) {
  if (!Number.isFinite(Number(value))) return "--";
  return `${Math.round(Math.max(0, Math.min(100, Number(value))))}%`;
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function stateSnapshot(state) {
  const raw = state?.raw || {};
  return {
    saved_at: new Date().toISOString(),
    mode: state?.mode || "unknown",
    tilt: Number(state?.tilt_risk ?? 0),
    readiness: Number(state?.readiness ?? 0),
    recovery: Number(state?.recovery ?? 0),
    confidence: Number(state?.signal_confidence ?? state?.confidence ?? 0),
    jaw_score: Number(raw.jaw_score ?? 0),
    shoulder_score: Number(raw.shoulder_score ?? 0),
    alert_level: state?.alert_level || "normal",
  };
}

function renderAutopilot(state) {
  if (!dom.autopilotText) return;
  const confidence = Number(state.signal_confidence ?? 0);
  const raw = state.raw || {};
  const tilt = Number(state.tilt_risk ?? 0);
  const jaw = Number(raw.jaw_score ?? 0);
  const shoulders = Number(raw.shoulder_score ?? 0);
  if (state.mode !== "live" || !state.numbers_visible || confidence < 0.55) {
    dom.autopilotEta.textContent = "нет сигнала";
    dom.autopilotText.textContent = "Жду чистый live-сигнал лица и плеч.";
    dom.autopilotSignal.textContent = "Autopilot не делает прогноз, когда данные stale/offline.";
    return;
  }
  if (tilt >= 70) {
    dom.autopilotEta.textContent = "сейчас";
    dom.autopilotText.textContent = "Тильт уже в красной зоне. Дать reset-команду.";
    dom.autopilotSignal.textContent = `Tilt ${Math.round(tilt)} · jaw ${Math.round(jaw)} · shoulders ${Math.round(shoulders)}`;
    return;
  }
  if (tilt >= 52 || jaw >= 58 || shoulders >= 58) {
    dom.autopilotEta.textContent = "~10 сек";
    dom.autopilotText.textContent = jaw >= shoulders
      ? "Вероятен jaw-lock. Предложить мягкую челюсть до alert."
      : "Вероятен shoulder raise. Предложить опустить плечи до alert.";
    dom.autopilotSignal.textContent = `Ранний паттерн: tilt ${Math.round(tilt)}, jaw ${Math.round(jaw)}, shoulders ${Math.round(shoulders)}.`;
    return;
  }
  dom.autopilotEta.textContent = "стабильно";
  dom.autopilotText.textContent = "Паттерн спокойный. Команда не нужна.";
  dom.autopilotSignal.textContent = "Система сохраняет тишину, пока нет раннего риска.";
}

function renderBaselineDrift(state) {
  if (!dom.baselineStatus || !dom.baselineDrift) return;
  const phases = playBaseline ? Object.keys(playBaseline).filter((key) => key !== "updated_at") : [];
  dom.baselineStatus.textContent = `${phases.length}/4 states`;
  if (!playBaseline?.neutral) {
    dom.baselineDrift.textContent = "Сохрани neutral, jaw, shoulders и release, чтобы сравнивать игрока с самим собой.";
    return;
  }
  const current = stateSnapshot(state);
  if (state.mode !== "live" || !state.numbers_visible) {
    dom.baselineDrift.textContent = "Baseline сохранён. Жду live-сигнал, чтобы показать drift относительно нормы.";
    return;
  }
  const jawDelta = current.jaw_score - Number(playBaseline.neutral.jaw_score || 0);
  const shoulderDelta = current.shoulder_score - Number(playBaseline.neutral.shoulder_score || 0);
  const worse = jawDelta > 12 || shoulderDelta > 12;
  dom.baselineDrift.textContent = worse
    ? `Сегодня выше baseline: челюсть +${Math.round(jawDelta)}, плечи +${Math.round(shoulderDelta)}.`
    : `Близко к baseline: челюсть ${Math.round(jawDelta)}, плечи ${Math.round(shoulderDelta)}.`;
}

function renderState(state) {
  lastState = state;
  const numbersVisible = Boolean(state.numbers_visible);
  const tiltScore = numbersVisible ? Number(state.tilt_risk) : null;
  const isLive = state.mode === "live";

  // Big number — show the actual tilt percentage when we trust the signal.
  // When stale/offline/low confidence: show -- and let the band say why.
  if (numbersVisible && Number.isFinite(tiltScore)) {
    dom.tiltValue.textContent = String(Math.round(tiltScore));
    dom.tiltSuffix.hidden = false;
    dom.tiltBand.textContent = tiltLabel(tiltScore).toUpperCase();
    dom.tiltBand.dataset.band =
      tiltScore >= 70 ? "high" : tiltScore >= 40 ? "medium" : "low";
  } else {
    dom.tiltValue.textContent = "--";
    dom.tiltSuffix.hidden = true;
    dom.tiltBand.textContent = t("play.tilt_offline") || "no signal";
    dom.tiltBand.dataset.band = "offline";
  }

  dom.tiltBar.style.width = numbersVisible && Number.isFinite(tiltScore)
    ? `${Math.min(100, Math.max(0, tiltScore))}%`
    : "0%";

  // Status line under the bar — always written in plain user-facing language.
  if (state.mode === "stale") {
    dom.tiltStatus.textContent = t("play.stale");
  } else if (state.mode === "offline") {
    dom.tiltStatus.textContent = t("play.engine_offline_long") || t("play.engine_offline");
  } else if (Number(state.signal_confidence ?? 0) < 0.55 && state.mode === "live") {
    dom.tiltStatus.textContent = t("play.confidence_low");
  } else if (state.mode === "live") {
    dom.tiltStatus.textContent = t("play.live_running") || "Live · the coach is watching.";
  } else {
    dom.tiltStatus.textContent = state.message || "";
  }

  // Show the engine-offline banner only when there's no live data flowing.
  if (dom.engineBanner) {
    dom.engineBanner.hidden = state.mode === "live" || state.mode === "demo";
  }

  // Coach line. When idle, show a friendly default instead of an empty dash.
  const recommendation = (state.recommendation || "").trim();
  if (state.mode === "live" && state.alert_level === "warning" && recommendation) {
    dom.commandText.textContent = recommendation;
    dom.commandMeta.textContent = t("play.command_meta_alert") || "Act in one breath";
  } else if (state.mode === "live" && state.alert_level === "recovery") {
    dom.commandText.textContent = recommendation || t("play.command_recovery") || "Stay soft";
    dom.commandMeta.textContent = t("play.command_meta_recovery") || "Recovery window";
  } else if (state.mode === "live") {
    dom.commandText.textContent = t("play.command_calm") || "Riding green";
    dom.commandMeta.textContent = t("play.command_meta_calm") || "No action needed";
  } else if (state.mode === "stale") {
    dom.commandText.textContent = t("play.command_stale") || "Engine paused";
    dom.commandMeta.textContent = "";
  } else {
    dom.commandText.textContent = t("play.command_offline") || "Start the cockpit";
    dom.commandMeta.textContent = "";
  }

  setMode(state.mode);
  dom.confidence.textContent = Number.isFinite(Number(state.signal_confidence))
    ? `${Math.round(Number(state.signal_confidence) * 100)}%`
    : "--";

  const showFeedback = state.alert_level === "warning" && isLive && numbersVisible;
  dom.feedback.hidden = !showFeedback;

  if (showFeedback && recommendation && recommendation !== lastSpokenAlert) {
    speak(recommendation, currentLang());
    lastSpokenAlert = recommendation;
  }

  lastAlertLevel = state.alert_level || "normal";
  lastConfidence = Number(state.signal_confidence ?? 0);
  renderAutopilot(state);
  renderBaselineDrift(state);
}

async function pollState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    const state = await response.json();
    renderState(state);
  } catch {
    renderState({ mode: "offline", message: t("play.engine_offline"), recommendation: "—" });
  }
}

// Server-Sent Events stream of /api/state. We try SSE first; if the
// connection fails twice in a row we fall back to ``setInterval(pollState)``.
// SSE is preferred because it's push-based, lower latency, and survives
// Render/Cloudflare without extra config.
let _sse = null;
let _sseRetries = 0;
let _sseFallbackTimer = null;
function startStateStream() {
  if (typeof EventSource === "undefined") {
    _sseFallbackTimer = setInterval(pollState, POLL_INTERVAL_MS);
    return;
  }
  try {
    _sse = new EventSource("/api/session-stream");
  } catch {
    _sseFallbackTimer = setInterval(pollState, POLL_INTERVAL_MS);
    return;
  }
  _sse.addEventListener("state", (event) => {
    _sseRetries = 0;
    try {
      renderState(JSON.parse(event.data));
    } catch {
      /* ignore */
    }
  });
  _sse.addEventListener("error", () => {
    _sseRetries += 1;
    if (_sseRetries >= 3) {
      try { _sse.close(); } catch { /* noop */ }
      if (!_sseFallbackTimer) {
        _sseFallbackTimer = setInterval(pollState, POLL_INTERVAL_MS);
      }
    }
  });
}

async function pollEngine() {
  try {
    const response = await fetch("/api/engine-health", { cache: "no-store" });
    const health = await response.json();
    const fps = health.heartbeat?.performance_profile?.fps;
    dom.fps.textContent = Number.isFinite(Number(fps)) ? Number(fps).toFixed(1) : "--";
  } catch {
    dom.fps.textContent = "--";
  }
}

async function pollLabels() {
  try {
    const response = await fetch("/api/cohort-summary", { cache: "no-store" });
    const stats = await response.json();
    const labelled = Number(stats.total_alerts_labelled ?? 0);
    dom.labels.textContent = `${labelled}`;
    if (dom.validationProgress) dom.validationProgress.textContent = `${labelled} / ${stats.validation_goal ?? LABEL_GOAL}`;
    if (dom.validationBar) dom.validationBar.style.width = `${Math.min(100, Number(stats.progress_percent ?? 0))}%`;
    if (dom.helpRate) dom.helpRate.textContent = stats.help_rate_percent == null ? "--" : percent(stats.help_rate_percent);
    if (dom.falseRate) dom.falseRate.textContent = stats.false_alert_rate_percent == null ? "--" : percent(stats.false_alert_rate_percent);
    if (dom.recentLabels) {
      const recent = stats.latest_testers || [];
      dom.recentLabels.innerHTML = recent.length
        ? recent.map((item) => `<li>${escapeHTML(item.label)} · ${escapeHTML(item.moment || "session")} · ${escapeHTML(item.tester || "tester")}</li>`).join("")
        : "<li>Пока нет labels. Во время сессии нажми T, H или F.</li>";
    }
  } catch {
    dom.labels.textContent = "--";
  }
}

async function pollPerformance() {
  try {
    const perf = await fetch("/api/performance-profile", { cache: "no-store" }).then((r) => r.json());
    const ready = await fetch("/api/demo-readiness", { cache: "no-store" }).then((r) => r.json());
    if (dom.demoReady) dom.demoReady.textContent = ready.ready_for_live_demo ? "готов" : "нужна настройка";
    if (dom.frameMs) dom.frameMs.textContent = Number.isFinite(Number(perf.frame_ms)) && Number(perf.frame_ms) > 0 ? `${Number(perf.frame_ms).toFixed(1)} ms` : "--";
    if (dom.cvMs) {
      const pose = Number(perf.pose_ms || 0);
      const face = Number(perf.face_ms || 0);
      dom.cvMs.textContent = pose || face ? `${pose.toFixed(1)} / ${face.toFixed(1)} ms` : "--";
    }
    if (dom.coachHealth) dom.coachHealth.textContent = perf.llm_status === "connected" ? "cloud coach отвечает" : "local fallback";
  } catch {
    if (dom.demoReady) dom.demoReady.textContent = "API недоступен";
  }
}

async function pollRecovery() {
  try {
    const response = await fetch("/api/command-effectiveness", { cache: "no-store" });
    const command = await response.json();
    if (dom.recoveryDelta) dom.recoveryDelta.textContent = command.tilt_delta ? `-${Math.round(command.tilt_delta)} pts` : "--";
    if (dom.recoveryCommand) dom.recoveryCommand.textContent = command.best_command || "Команда появится после live alert.";
    if (dom.recoverySeconds) dom.recoverySeconds.textContent = command.recovery_seconds ? `${Math.round(command.recovery_seconds)}` : "--";
  if (dom.recoverySource) dom.recoverySource.textContent = command.source || command.status || "--";
  } catch {
    if (dom.recoveryDelta) dom.recoveryDelta.textContent = "--";
  }
}

async function pollPersonalAndAutopilot() {
  try {
    const [personal, autopilot] = await Promise.all([
      fetch("/api/personal-model-score?id=default", { cache: "no-store" }).then((r) => r.json()),
      fetch("/api/autopilot-v2", { cache: "no-store" }).then((r) => r.json()),
    ]);
    if (dom.personalScore) {
      dom.personalScore.textContent = personal.score == null ? "--" : `${Math.round(personal.score)} / 100`;
    }
    if (dom.personalMessage) dom.personalMessage.textContent = personal.message || "personal model score";
    if (autopilot.status === "ready" && dom.autopilotText) {
      dom.autopilotEta.textContent = autopilot.lead_time_seconds == null ? autopilot.intervention : `~${autopilot.lead_time_seconds} сек`;
      dom.autopilotText.textContent = autopilot.prediction || dom.autopilotText.textContent;
      dom.autopilotSignal.textContent = `${autopilot.command || ""} · driver: ${autopilot.driver || "n/a"} · risk ${Math.round(autopilot.risk || 0)}`;
    }
  } catch {
    // The local browser baseline view remains active without these APIs.
  }
}

async function saveBaselineToServer() {
  if (!playBaseline) return;
  try {
    await kaiPost("/api/baseline-profile", {
      profile_id: "default",
      baseline: playBaseline,
      source: "play_page",
    });
  } catch {
    // Browser-local baseline remains useful even if the site server is unavailable.
  }
}

async function sendFeedback(key, hint = "") {
  const payload = {
    source: "play_page",
    moment: hint,
    felt_tension: key === "felt_tension",
    helped: key === "helped",
    false_alert: key === "false_alert",
    last_alert_level: lastAlertLevel,
    confidence: lastConfidence,
    lang: currentLang(),
    state: lastState,
    baseline_summary: playBaseline
      ? { phases: Object.keys(playBaseline).filter((key) => key !== "updated_at") }
      : null,
  };
  try {
    const result = await kaiPost("/api/feedback", payload);
    if (result && result.ok) {
      dom.feedbackStatus.textContent = t("play.feedback_thanks");
    } else if (result && result.error === "rate_limited") {
      dom.feedbackStatus.textContent = `${t("play.feedback_thanks")} (rate-limited)`;
    } else {
      dom.feedbackStatus.textContent = "—";
    }
  } catch {
    dom.feedbackStatus.textContent = "—";
  }
  pollLabels();
}

function bindFeedbackButtons() {
  dom.feedback.querySelectorAll("button[data-key]").forEach((btn) => {
    btn.addEventListener("click", () => sendFeedback(btn.dataset.key, "ui_click"));
  });
}

function bindBaselineButtons() {
  dom.baselineButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const phase = btn.dataset.baseline;
      playBaseline = {
        ...(playBaseline || {}),
        [phase]: stateSnapshot(lastState || {}),
        updated_at: new Date().toISOString(),
      };
      localStorage.setItem(BASELINE_KEY, JSON.stringify(playBaseline));
      dom.feedbackStatus.textContent = `Baseline ${phase} сохранён локально.`;
      saveBaselineToServer();
      renderBaselineDrift(lastState || {});
    });
  });
}

function bindHotkeys() {
  document.addEventListener("keydown", (event) => {
    if (event.target && (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA")) return;
    if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "m") {
      event.preventDefault();
      toggleMute();
      setMuteLabel();
      return;
    }
    const key = event.key.toLowerCase();
    if (key === "t") sendFeedback("felt_tension", "hotkey");
    if (key === "h") sendFeedback("helped", "hotkey");
    if (key === "f") sendFeedback("false_alert", "hotkey");
  });
}

function bindToggles() {
  dom.muteToggle.addEventListener("click", () => {
    toggleMute();
    setMuteLabel();
  });
  dom.reliabilityToggle?.addEventListener("click", () => {
    reliabilityMode = !reliabilityMode;
    localStorage.setItem("kinaesthetic_reliability_mode", reliabilityMode ? "1" : "0");
    setReliabilityLabel();
    dom.feedbackStatus.textContent = reliabilityMode
      ? "Investor mode: показываем только стабильные live/demo состояния и fallback coach."
      : "";
  });
  dom.langToggle.addEventListener("click", () => {
    toggleLang();
    setMuteLabel();
  });
  dom.shareBtn.addEventListener("click", async () => {
    dom.shareBtn.disabled = true;
    try {
      const result = await saveProofPng();
      dom.feedbackStatus.textContent = result.ok ? t("play.share_saved") : t("play.share_unavailable");
    } finally {
      dom.shareBtn.disabled = false;
    }
  });
}

async function init() {
  applyTranslations();
  setMuteLabel();
  setReliabilityLabel();
  const accepted = await requireConsent();
  if (!accepted) {
    dom.tiltStatus.textContent = t("play.consent_decline");
    return;
  }
  bindFeedbackButtons();
  bindBaselineButtons();
  bindHotkeys();
  bindToggles();
  pollState();
  pollEngine();
  pollLabels();
  pollRecovery();
  pollPerformance();
  pollPersonalAndAutopilot();
  startStateStream();
  setInterval(pollEngine, STATS_INTERVAL_MS);
  setInterval(pollLabels, STATS_INTERVAL_MS);
  setInterval(pollRecovery, STATS_INTERVAL_MS);
  setInterval(pollPerformance, STATS_INTERVAL_MS);
  setInterval(pollPersonalAndAutopilot, STATS_INTERVAL_MS);
}

init();
