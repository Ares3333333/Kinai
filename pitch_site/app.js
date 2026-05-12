const tensionLabels = { low: "низкое", medium: "среднее", high: "высокое" };
const statusLabels = {
  live: "LIVE · идёт анализ",
  demo: "Демо-режим",
  stale: "Live state устарел",
  offline: "Движок не подключён",
  idle: "Движок подключён · сессия не запущена",
};

const hasNumber = (v) => v !== null && v !== undefined && Number.isFinite(Number(v));
const clampPercent = (v) => (hasNumber(v) ? Math.max(0, Math.min(100, Number(v))) : 0);
const showPercent = (v) => (hasNumber(v) ? `${Math.round(clampPercent(v))}%` : "--");
const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
const setWidth = (id, value) => { const el = document.getElementById(id); if (el) el.style.width = `${clampPercent(value)}%`; };

async function refreshState() {
  try {
    const [stateResp, healthResp] = await Promise.all([
      fetch("/api/state", { cache: "no-store" }),
      fetch("/api/system-health", { cache: "no-store" }),
    ]);
    if (!stateResp.ok) throw new Error("state unavailable");
    const state = await stateResp.json();
    const health = healthResp.ok ? await healthResp.json() : null;
    renderState(state, health);
  } catch {
    renderState(
      { mode: "offline", session_status: "offline", recommendation: "Запустите локальный engine или откройте pitch demo." },
      null,
    );
  }
}

function renderState(state, health) {
  const statusKey = state.session_status === "idle" ? "idle" : state.mode || state.session_status;
  setText("sessionStatus", statusLabels[statusKey] || "Состояние продукта");
  if (state.mode === "live" && state.numbers_visible !== false) {
    setText("tiltRisk", showPercent(state.tilt_risk));
    setText("readiness", showPercent(state.readiness));
    setText("recovery", showPercent(state.recovery));
    setText("jaw", tensionLabels[state.jaw_tension] || state.jaw_tension || "--");
    setText("shoulders", tensionLabels[state.shoulder_tension] || state.shoulder_tension || "--");
    setWidth("tiltBar", state.tilt_risk);
  } else {
    setText("tiltRisk", "--");
    setText("readiness", "--");
    setText("recovery", "--");
    setText("jaw", "--");
    setText("shoulders", "--");
    setWidth("tiltBar", 0);
  }
  setText("recommendation", state.recommendation || "Готов");
  if (health) {
    const llm = health.llm || {};
    setText("llmStatus", `${llm.connected ? "connected" : llm.status || "fallback"} · ${llm.provider || "local"} · ${llm.latency_ms ?? "—"}мс`);
  }
}

refreshState();
setInterval(refreshState, 1000);
