const pct = (value) => `${Math.round(Math.max(0, Math.min(100, Number(value) || 0)))}%`;
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#039;",
}[char]));

function card(title, body) {
  return `<article class="content-card"><strong>${escapeHTML(title)}</strong><p>${escapeHTML(body || "--")}</p></article>`;
}

function chartRow(label, value) {
  return `<div class="report-row"><span>${label}</span><i style="width:${pct(value)}"></i><strong>${pct(value)}</strong></div>`;
}

function renderReplay(replay) {
  const root = document.getElementById("sessionReplay");
  if (!root) return;
  const points = replay.points || [];
  if (!points.length) {
    root.innerHTML = `<p class="muted">${escapeHTML(replay.message || "No replay data yet. Start a live session.")}</p>`;
    return;
  }
  const width = 980;
  const height = 300;
  const pad = 34;
  const maxX = Math.max(points.length - 1, 1);
  const xy = (point) => {
    const x = pad + (Number(point.i) / maxX) * (width - pad * 2);
    const y = height - pad - (Math.max(0, Math.min(100, Number(point.tilt) || 0)) / 100) * (height - pad * 2);
    return [x, y];
  };
  const line = points.map((point) => xy(point).join(",")).join(" ");
  const markerSvg = (replay.markers || []).map((marker) => {
    const point = points[Math.max(0, Math.min(points.length - 1, Number(marker.i) || 0))] || points[points.length - 1];
    const [x, y] = xy(point);
    const klass = marker.type === "peak" ? "peak" : marker.type === "recovery" ? "recovery" : "label";
    const labelY = Math.max(18, y - 14);
    return `<g class="replay-marker ${klass}">
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5"></circle>
      <text x="${x.toFixed(1)}" y="${labelY.toFixed(1)}">${escapeHTML(marker.label)}</text>
    </g>`;
  }).join("");
  root.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Tilt replay timeline">
      <defs>
        <linearGradient id="tiltReplayGradient" x1="0" x2="1">
          <stop offset="0%" stop-color="#35f29a" />
          <stop offset="60%" stop-color="#ffb84d" />
          <stop offset="100%" stop-color="#ff4f58" />
        </linearGradient>
      </defs>
      <line class="replay-grid-line" x1="${pad}" x2="${width - pad}" y1="${height - pad}" y2="${height - pad}"></line>
      <line class="replay-grid-line warning" x1="${pad}" x2="${width - pad}" y1="${height - pad - 0.7 * (height - pad * 2)}" y2="${height - pad - 0.7 * (height - pad * 2)}"></line>
      <polyline class="replay-line" points="${line}"></polyline>
      ${markerSvg}
    </svg>
    <p class="muted">Samples: ${points.length} - peak ${replay.peak_tilt ?? "--"} - recovery low ${replay.recovery_tilt ?? "--"}</p>
  `;
}

async function loadReport() {
  const report = await fetch("/api/report", { cache: "no-store" }).then((r) => r.json());
  const chart = document.getElementById("reportChart");
  chart.innerHTML = [
    chartRow("Peak tilt", report.peak_tilt || 0),
    chartRow("Final tilt", report.final_tilt || 0),
    chartRow("Readiness", report.avg_readiness || 0),
    chartRow("Recovery delta", report.recovery_delta || 0),
  ].join("");

  document.getElementById("reportCards").innerHTML = [
    card("What triggered tilt", report.what_triggered_tilt),
    card("When the pattern started", report.pattern_started_at || "not detected"),
    card("Recovery speed", report.recovery_seconds ? `${report.recovery_seconds} sec` : "need more live data"),
    card("Command that worked", report.command_that_worked),
    card("Personal somatic pattern", report.personal_somatic_pattern),
    card("Samples", `${report.samples || 0} data points`),
  ].join("");

  const proof = report.proof_card || {};
  document.getElementById("reportProof").innerHTML = `<span>Recovery proof</span><h2>${proof.headline || "Tilt -- -> --"}</h2><p>${proof.jaw || ""} - ${proof.shoulders || ""}<br>${proof.command || ""}</p>`;

  const similar = report.similar_states || [];
  document.getElementById("similarStates").innerHTML = similar.length
    ? similar.map((item) => card(`${Math.round(item.similarity * 100)}% similar`, `${item.phrase || item.tokens?.join(" + ") || "body-state"} - Tilt ${item.tilt}`)).join("")
    : card("No similar states yet", "Start a live session to collect embeddings.");

  try {
    const command = await fetch("/api/command-effectiveness", { cache: "no-store" }).then((r) => r.json());
    document.getElementById("commandEffectiveness").innerHTML = [
      card("Best command", command.best_command),
      card("Tilt delta", command.tilt_delta ? `-${command.tilt_delta} tilt points` : command.message),
      card("Recovery delta", command.tilt_delta ? `-${command.tilt_delta} tilt points` : command.message),
    ].join("");
  } catch {
    document.getElementById("commandEffectiveness").innerHTML = card("Command Effectiveness", "API unavailable.");
  }
}

async function exportDeck() {
  const status = document.getElementById("exportStatus");
  status.textContent = "Building founder deck package...";
  try {
    const result = await fetch("/api/export-founder-deck", { cache: "no-store" }).then((r) => r.json());
    status.textContent = result.ok ? `Ready: ${result.export_dir}` : "Could not build export.";
  } catch {
    status.textContent = "Export API unavailable.";
  }
}

async function loadReplay() {
  const replay = await fetch("/api/session-replay", { cache: "no-store" }).then((r) => r.json());
  renderReplay(replay);
}

document.getElementById("exportDeck")?.addEventListener("click", exportDeck);
loadReplay().catch(() => {
  const root = document.getElementById("sessionReplay");
  if (root) root.innerHTML = "<p class=\"muted\">Replay API unavailable.</p>";
});
loadReport().catch(() => {
  document.getElementById("reportCards").innerHTML = card("No data", "Start a live session or pitch demo.");
});
