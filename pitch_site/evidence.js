const q = (id) => document.getElementById(id);

const publicFallbackEvidence = {
  public_demo_mode: true,
  public_session: {
    samples: 0,
    proof: {
      headline: "Demo proof available",
      details: "Scripted demo shows signal -> alert -> recovery. Run /play for live browser CV.",
    },
  },
  investor_metrics: {
    body_state_samples: 0,
    embedding_vectors: 0,
    feedback_labels: 0,
  },
  cohort: {
    total_alerts_labelled: 0,
    unique_testers: 0,
    tester_target: 100,
    labels_target: 150,
    help_rate_percent: null,
    false_alert_rate_percent: null,
  },
  cloud_coach: {
    provider: "fallback",
    model: "local templates",
    status: "offline",
    latency_ms: null,
  },
  public_timeline: {
    points: [
      { t: 0, tilt_risk: 24, readiness: 82, recovery: 20 },
      { t: 8, tilt_risk: 46, readiness: 66, recovery: 30 },
      { t: 16, tilt_risk: 74, readiness: 43, recovery: 38 },
      { t: 28, tilt_risk: 31, readiness: 79, recovery: 78 },
    ],
  },
  hosted_storage: {
    configured: false,
    provider: "browser/public demo",
  },
};

let latestEvidence = publicFallbackEvidence;
let latestHealth = {
  mode: "offline",
  public_demo_mode: true,
  message: "Public demo mode: backend is not connected.",
};

function setText(id, value) {
  const node = q(id);
  if (node) node.textContent = value ?? "-";
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${Math.round(Number(value))}%`;
}

function pathFor(points, key, width, height, pad) {
  if (!points.length) return "";
  const maxT = Math.max(...points.map((p) => Number(p.t) || 0), 1);
  return points
    .map((point, index) => {
      const x = pad + ((Number(point.t) || 0) / maxT) * (width - pad * 2);
      const y = pad + (1 - Math.max(0, Math.min(100, Number(point[key]) || 0)) / 100) * (height - pad * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

function setRuntimeMode(mode, extra = {}) {
  const node = q("runtimeMode");
  const hint = q("modeHint");
  const normalized = String(mode || "offline").toLowerCase();
  const labels = { live: "LIVE", demo: "DEMO", stale: "STALE", offline: "OFFLINE" };
  if (node) {
    node.textContent = extra.public_demo_mode ? "PUBLIC DEMO" : labels[normalized] || normalized.toUpperCase();
    node.dataset.state = extra.public_demo_mode ? "demo" : normalized;
  }
  if (!hint) return;
  hint.dataset.mode = extra.public_demo_mode ? "demo" : normalized;
  hint.textContent =
    normalized === "live"
      ? "LIVE: evidence is based on fresh live signals."
      : normalized === "demo" || extra.public_demo_mode
        ? "PUBLIC DEMO: backend is optional here. Demo/evidence stay honest; /play runs browser CV locally."
        : normalized === "stale"
          ? "STALE: live signal is outdated. Old numbers are not presented as live."
          : "OFFLINE: no live backend signal. Use /demo for the investor story or /play for local camera CV.";
}

function renderChart(timeline) {
  const svg = q("sessionChart");
  if (!svg) return;
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-labelledby", "sessionChartTitle sessionChartDesc");
  const points = timeline?.points || [];
  const width = 900;
  const height = 320;
  const pad = 34;
  if (!points.length) {
    svg.innerHTML = `
      <title id="sessionChartTitle">No live timeline yet</title>
      <desc id="sessionChartDesc">Open the investor demo or start live browser camera to create a signal timeline.</desc>
      <text x="34" y="170" fill="rgba(255,255,255,.62)" font-size="22">No live timeline yet. Open /demo or start /play.</text>
    `;
    setText("chartDetails", "Timeline appears after demo/live data. Raw video/audio is never stored.");
    return;
  }
  const tilt = pathFor(points, "tilt_risk", width, height, pad);
  const readiness = pathFor(points, "readiness", width, height, pad);
  const recovery = pathFor(points, "recovery", width, height, pad);
  const last = points[points.length - 1] || {};
  svg.innerHTML = `
    <title id="sessionChartTitle">Tilt, readiness, and recovery timeline</title>
    <desc id="sessionChartDesc">Latest values: tilt ${Math.round(Number(last.tilt_risk) || 0)}, readiness ${Math.round(Number(last.readiness) || 0)}, recovery ${Math.round(Number(last.recovery) || 0)}. Raw video and audio are not stored.</desc>
    <rect x="0" y="0" width="${width}" height="${height}" rx="18" fill="rgba(255,255,255,.025)" />
    <line x1="${pad}" x2="${width - pad}" y1="${height - pad}" y2="${height - pad}" stroke="rgba(255,255,255,.14)" />
    <path d="${readiness}" fill="none" stroke="#8ee8ff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" opacity=".9" />
    <path d="${recovery}" fill="none" stroke="#35f29a" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" opacity=".9" />
    <path d="${tilt}" fill="none" stroke="#ff4f58" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" />
  `;
  setText(
    "chartDetails",
    `${points.length} timeline points. Latest: tilt ${Math.round(Number(last.tilt_risk) || 0)}, readiness ${Math.round(Number(last.readiness) || 0)}, recovery ${Math.round(Number(last.recovery) || 0)}. Raw video/audio not stored.`
  );
}

async function fetchJsonOrFallback(url, fallback, timeoutMs = 2500) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { cache: "no-store", signal: controller.signal });
    if (!response.ok) return fallback;
    return await response.json();
  } catch {
    return fallback;
  } finally {
    clearTimeout(timeout);
  }
}

function renderEvidence(evidence) {
  latestEvidence = evidence || publicFallbackEvidence;
  const publicSession = latestEvidence.public_session || {};
  const proof = publicSession.proof || latestEvidence.proof_card || {};
  const metrics = latestEvidence.investor_metrics || {};
  const cohort = latestEvidence.cohort || {};
  const coach = latestEvidence.cloud_coach || {};
  const timeline = latestEvidence.public_timeline || {};
  const storage = latestEvidence.hosted_storage || {};
  const isPublicDemo = Boolean(latestEvidence.public_demo_mode || latestHealth.public_demo_mode);
  const labelTarget = Number(cohort.labels_target || latestEvidence.next_milestone?.labels_target || 150);
  const testerTarget = Number(cohort.tester_target || latestEvidence.next_milestone?.tester_target || 100);
  const labelCount = Number(cohort.total_alerts_labelled ?? metrics.feedback_labels ?? 0);
  const testerCount = Number(cohort.unique_testers ?? 0);
  const testerRemaining = Math.max(0, testerTarget - testerCount);
  const storageConfigured = Boolean(storage.configured);

  setText("evidenceStatus", isPublicDemo ? "public demo ready" : "ready");
  setText("storageStatus", storage.configured ? `storage: ${storage.provider}` : "storage: local/browser");
  setText("proofHeadline", proof.headline || "Demo proof available");
  setText(
    "proofDetails",
    publicSession.samples
      ? `Samples: ${publicSession.samples}. Recovery delta: ${publicSession.recovery_delta}. Avg readiness: ${publicSession.avg_readiness ?? "-"}.`
      : proof.details || "Open /demo for scripted proof or /play for local browser CV."
  );
  setText("coachProvider", coach.provider || "fallback");
  setText("coachDetails", `${coach.model || "local templates"}; ${coach.status || "fallback"}; latency ${coach.latency_ms ?? "-"}ms`);
  setText("labelCount", `${labelCount}/${labelTarget} labels`);
  setText(
    "labelDetails",
    `Help-rate: ${pct(cohort.help_rate_percent)}. False alerts: ${pct(cohort.false_alert_rate_percent)}. Testers: ${testerCount}/${testerTarget}.`
  );
  setText("testerBaseCount", `${testerCount}/${testerTarget} testers`);
  setText(
    "testerBaseDetails",
    testerRemaining
      ? `${testerRemaining} testers left to reach the first validation base. Sessions save derived samples, labels, and proof metadata only.`
      : "First validation base reached. Continue collecting labels and recovery proof; raw media is never stored."
  );
  setText("autoSaveMode", storageConfigured ? `${storage.provider || "Supabase"} database` : "Local JSONL");
  setText(
    "autoSaveDetails",
    storageConfigured
      ? `Auto-save is mirrored to ${storage.table || "hosted storage"} with local JSONL fallback.`
      : "Auto-save is active on this backend. For the public beta, enable the Supabase mirror so every derived event is copied into hosted storage."
  );
  setText("sampleCount", `${metrics.body_state_samples ?? 0}`);
  setText("sampleDetails", `Embeddings: ${metrics.embedding_vectors ?? 0}. Public samples: ${publicSession.samples ?? 0}.`);
  setText(
    "exportStatus",
    isPublicDemo
      ? "Public demo mode: download a client-side proof package. No raw media included."
      : "Ready to export investor package. No raw media included."
  );
  renderChart(timeline);
}

function buildEvidencePackage(source = "browser") {
  return {
    product: "Kinaesthetic AI",
    package_type: "investor_evidence_package",
    source,
    generated_at: new Date().toISOString(),
    public_url: window.location.origin,
    demo_url: `${window.location.origin}/demo`,
    live_url: `${window.location.origin}/play`,
    privacy_url: `${window.location.origin}/privacy`,
    backend_mode: latestHealth.mode || "offline",
    public_demo_mode: Boolean(latestEvidence.public_demo_mode || latestHealth.public_demo_mode),
    proof: latestEvidence.public_session?.proof || latestEvidence.proof_card || {
      headline: "Tilt 74 -> 31",
      details: "Demo scenario: signal -> alert -> recovery proof.",
    },
    metrics: latestEvidence.investor_metrics || {},
    validation: latestEvidence.cohort || {},
    storage: latestEvidence.hosted_storage || {},
    privacy: {
      raw_video: "not stored",
      raw_audio: "not stored",
      camera_frames: "not stored",
      stored_data: ["derived body-state signals", "session events", "tester labels", "proof metadata"],
      positioning: "performance coaching, not medical diagnosis",
    },
  };
}

function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function showExportSuccess(mode) {
  const node = q("exportSuccess");
  const details = q("exportSuccessDetails");
  if (!node) return;
  node.hidden = false;
  if (details) {
    details.textContent = mode === "backend"
      ? "Backend export plus browser proof JSON are ready. Package includes proof, validation summary, links, and privacy passport. Raw media is not included."
      : "Client-side proof JSON is ready for public demo sharing. Package includes proof, validation summary, links, and privacy passport. Raw media is not included.";
  }
  node.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function refreshSystemHealth() {
  latestHealth = await fetchJsonOrFallback("/api/system-health", latestHealth);
  setRuntimeMode(latestHealth?.mode || "offline", latestHealth || {});
}

async function exportPitchPackage() {
  const button = q("exportPitchPackage");
  if (button) {
    button.disabled = true;
    button.textContent = "Exporting...";
  }
  const backendExport = await fetchJsonOrFallback("/api/export-founder-deck", null, 1200);
  const payload = buildEvidencePackage(backendExport?.export_dir ? "backend+browser" : "browser");
  payload.backend_export = backendExport || { ok: false, mode: "client_only" };
  downloadJson(`kinaesthetic_ai_evidence_${Date.now()}.json`, payload);
  showExportSuccess(backendExport?.export_dir ? "backend" : "browser");
  setText(
    "exportStatus",
    backendExport?.export_dir
      ? `Downloaded proof JSON. Backend export: ${backendExport.export_dir}; raw media: none.`
      : "Downloaded proof JSON in public demo mode. Backend not required; raw media: none."
  );
  if (button) {
    button.disabled = false;
    button.textContent = "Export package";
  }
}

async function loadEvidence() {
  const evidence = await fetchJsonOrFallback("/api/evidence", publicFallbackEvidence);
  renderEvidence(evidence);
}

q("exportPitchPackage")?.addEventListener("click", exportPitchPackage);
q("downloadLocalEvidence")?.addEventListener("click", () => {
  downloadJson(`kinaesthetic_ai_public_proof_${Date.now()}.json`, buildEvidencePackage("browser"));
  setText("exportStatus", "Downloaded local proof JSON. No raw media included.");
  showExportSuccess("browser");
});

refreshSystemHealth();
loadEvidence();
setInterval(refreshSystemHealth, 5000);
setInterval(loadEvidence, 7000);
