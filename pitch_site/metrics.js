const setText = (id, value) => {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
};

const fmt = (value) => new Intl.NumberFormat("ru-RU").format(Number(value) || 0);
const pct = (value) => `${Math.round(Math.max(0, Math.min(100, Number(value) || 0)))}%`;
const escapeHTML = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);

function setBar(id, value, max) {
  const node = document.getElementById(id);
  if (!node) return;
  const percent = max > 0 ? (Number(value) / max) * 100 : 0;
  node.style.width = pct(percent);
}

function renderFiles(files) {
  const root = document.getElementById("dataFiles");
  if (!root) return;
  root.innerHTML = files.length
    ? files
        .map(
          (file) => `
      <article>
        <strong>${escapeHTML(file.file)}</strong>
        <span>${fmt(file.bytes)} bytes</span>
        <p>${file.contains_sensitive_derived_signals ? "Derived body-state signals" : "Metadata / summary file"}</p>
      </article>
    `
        )
        .join("")
    : "<article><strong>No dataset yet</strong><p>Start /play or /demo to create derived records. Raw media is never stored.</p></article>";
}

function renderCommandTable(rows) {
  const root = document.getElementById("commandTable");
  if (!root) return;
  root.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
      <article>
        <strong>${escapeHTML(row.command || "local coach command")}</strong>
        <span>${escapeHTML(row.claim || row.source || "needs labels")}</span>
        <p>uses: ${fmt(row.uses)} - delta: ${row.avg_tilt_delta == null ? "--" : `-${row.avg_tilt_delta} pts`} - help: ${row.help_rate_percent == null ? "--" : pct(row.help_rate_percent)} - false: ${row.false_alert_rate_percent == null ? "--" : pct(row.false_alert_rate_percent)}</p>
      </article>
    `
        )
        .join("")
    : "<article><strong>No command evidence yet</strong><p>Collect live sessions and H/F feedback labels.</p></article>";
}

async function fetchJson(url, fallback = {}) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) return fallback;
    return await response.json();
  } catch {
    return fallback;
  }
}

async function loadMetrics() {
  const metrics = await fetchJson("/api/investor-metrics");
  const storage = await fetchJson("/api/storage-health", { configured: false, provider: "local" });

  setText("samplesCount", fmt(metrics.body_state_samples));
  setText("sessionsCount", fmt(metrics.sessions_recorded));
  setText("embeddingsCount", fmt(metrics.embedding_vectors));
  setText("labelsCount", fmt(metrics.feedback_labels));
  setText("proofHeadline", metrics.proof_headline || "Tilt -- -> --");
  setText("proofMeta", metrics.best_command || "Command appears after a live session.");
  setText("recoveryDelta", metrics.recovery_delta ? `${metrics.recovery_delta} pts` : "--");
  setText("recoverySeconds", metrics.recovery_seconds ? `${metrics.recovery_seconds} sec` : "--");
  setText("commandEffectiveness", metrics.tilt_delta_points ? `-${metrics.tilt_delta_points} pts` : "--");
  setText("storageMode", storage.configured ? `${storage.provider || "Supabase"} database` : "Local JSONL");
  setText(
    "storageDetails",
    storage.configured
      ? `Hosted mirror is configured for ${storage.table || "kinaesthetic_events"}. Local JSONL stays as fallback.`
      : "Auto-save works on this backend now. For public beta, turn on the Supabase mirror so every derived event is copied into hosted storage."
  );

  renderFiles(metrics.data_files || []);
}

async function loadCohort() {
  const cohort = await fetchJson("/api/cohort-summary");
  const labels = Number(cohort.total_alerts_labelled || 0);
  const labelGoal = Number(cohort.validation_goal || 150);
  const testers = Number(cohort.unique_testers || 0);
  const testerGoal = Number(cohort.tester_target || 100);
  const testersLeft = Math.max(0, testerGoal - testers);

  setText("validationHeadline", `${fmt(testers)} / ${fmt(testerGoal)} testers`);
  setText(
    "validationDetails",
    testersLeft
      ? `${fmt(testersLeft)} testers left to reach the first 100+ validation base. Labels goal: ${fmt(labels)} / ${fmt(labelGoal)}.`
      : `First tester base reached. Labels goal: ${fmt(labels)} / ${fmt(labelGoal)}.`
  );
  setText("cohortTesters", `${fmt(testers)} / ${fmt(testerGoal)}`);
  setText("testerProgress", `${fmt(testersLeft)} testers left`);
  setBar("testerBar", testers, testerGoal);
  setText("cohortLabels", `${fmt(labels)} / ${fmt(labelGoal)}`);
  setText("cohortProgress", `${fmt(cohort.labels_remaining)} labels left`);
  setBar("cohortBar", labels, labelGoal);
  setText("cohortHelpRate", cohort.help_rate_percent == null ? "--" : pct(cohort.help_rate_percent));
  setText("cohortFalseRate", cohort.false_alert_rate_percent == null ? "--" : pct(cohort.false_alert_rate_percent));

  const root = document.getElementById("cohortRecent");
  if (!root) return;
  const recent = cohort.latest_testers || [];
  root.innerHTML = recent.length
    ? recent
        .map((item) => `<li><strong>${escapeHTML(item.label)}</strong><span>${escapeHTML(item.moment || "session")} - ${escapeHTML(item.tester || "tester")}</span></li>`)
        .join("")
    : "<li>No labels yet. Open /play and press T/H/F during a session.</li>";
}

async function loadCommandTable() {
  const table = await fetchJson("/api/command-effectiveness-table", { rows: [] });
  renderCommandTable(table.rows || []);
}

async function loadFalseAlerts() {
  const review = await fetchJson("/api/false-alert-review", { recent: [] });
  const root = document.getElementById("falseAlertReview");
  if (!root) return;
  const rows = review.recent || [];
  root.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
      <article>
        <strong>${escapeHTML(row.moment || "false alert")}</strong>
        <span>${escapeHTML(row.likely_reason || "review")}</span>
        <p>mode: ${escapeHTML(row.mode || "unknown")} - confidence: ${row.confidence ?? "--"} - tilt: ${row.tilt ?? "--"}</p>
      </article>
    `
        )
        .join("")
    : `<article><strong>No false alerts marked yet</strong><p>${escapeHTML(review.suggested_action || "Press F during testing when the alert is wrong.")}</p></article>`;
}

function refreshAll() {
  loadMetrics().catch(() => setText("samplesCount", "--"));
  loadCohort().catch(() => setText("cohortLabels", "--"));
  loadCommandTable().catch(() => renderCommandTable([]));
  loadFalseAlerts().catch(() => {});
}

document.getElementById("refreshMetrics")?.addEventListener("click", refreshAll);
refreshAll();
