// /admin dashboard -- read-only ops view of the data lake.
import { kaiGet } from "/kai_api.js";

const fmt = (value) =>
  value === null || value === undefined ? "--" : new Intl.NumberFormat("en").format(Number(value) || 0);

const fmtTime = (value) => {
  if (!value) return "--";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
};

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value ?? "--";
}

function setPill(node, kind, label) {
  node.className = `admin-pill pill-${kind}`;
  node.textContent = label;
}

async function load() {
  const pill = document.getElementById("adminStatusPill");
  setPill(pill, "warn", "loading...");
  let summary;
  try {
    summary = await kaiGet("/api/admin-summary");
  } catch (err) {
    setPill(pill, "err", `error: ${err}`);
    return;
  }
  if (!summary) {
    setPill(pill, "err", "empty response");
    return;
  }

  const data = summary.data || {};
  setText("adminSessions", fmt(data.sessions?.count));
  setText("adminSessionsAt", `last: ${fmtTime(data.sessions?.last_at)}`);
  setText("adminFeedback", fmt(data.feedback?.count));
  setText("adminFeedbackAt", `last: ${fmtTime(data.feedback?.last_at)}`);
  setText("adminCoachMem", fmt(data.coach_memory?.count));
  setText("adminCoachMemAt", `last: ${fmtTime(data.coach_memory?.last_at)}`);
  setText("adminConsent", fmt(data.consent?.count));
  setText("adminConsentAt", `last: ${fmtTime(data.consent?.last_at)}`);

  const engine = summary.engine || {};
  const engineConnected = engine.engine_connected === true;
  setText("adminEngine", engineConnected ? "connected" : "offline");
  setText(
    "adminEngineMeta",
    `heartbeat: ${engine.heartbeat_age_seconds ?? "--"}s - overlay: ${engine.overlay_state_age_seconds ?? "--"}s - running: ${engine.overlay_running ? "yes" : "no"}`
  );

  const stats = summary.feedback_stats || {};
  setText("adminHelpRate", stats.help_rate_percent != null ? `${stats.help_rate_percent}%` : "--");
  setText(
    "adminLabelTotals",
    `helped ${stats.labels?.helped ?? 0} - tense ${stats.labels?.felt_tension ?? 0} - false ${stats.labels?.false_alert ?? 0}`
  );
  setText("adminFalseRate", stats.false_alert_rate_percent != null ? `${stats.false_alert_rate_percent}%` : "--");

  const sec = summary.security || {};
  setText("adminAuth", sec.auth_enabled ? "token + consent" : "DEV (no auth)");
  setText(
    "adminAuthMeta",
    `consent ${sec.consent_version || "--"} - write ${sec.rate_limit_write_per_minute || "?"}/min - read ${sec.rate_limit_read_per_minute || "?"}/min`
  );
  document.getElementById("adminOrigins").textContent = JSON.stringify(sec.allowed_origins || [], null, 2);
  document.getElementById("adminDeprecated").textContent = JSON.stringify(summary.deprecated_routes || {}, null, 2);
  document.getElementById("adminRaw").textContent = JSON.stringify(summary, null, 2);

  const overall = engineConnected && (stats.false_alert_rate_percent ?? 0) < 30 ? "ok" : engineConnected ? "warn" : "err";
  setPill(pill, overall, overall === "ok" ? "healthy" : overall === "warn" ? "needs attention" : "engine offline");
}

document.getElementById("adminRefresh")?.addEventListener("click", load);
load();
setInterval(load, 30_000);
