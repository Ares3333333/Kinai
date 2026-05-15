const fallback = (path) => {
  const now = new Date().toISOString();
  if (path === "system-health") {
    return {
      mode: "offline",
      is_live: false,
      is_demo: false,
      backend: "not_configured",
      public_demo_mode: true,
      numbers_visible: false,
      camera_active: false,
      engine_connected: false,
      signal_confidence: 0,
      message: "Public demo mode: backend is not connected. /demo and local browser camera still work.",
      llm: { connected: false, status: "fallback", provider: "local", latency_ms: null },
      checked_at: now
    };
  }
  if (path === "state") {
    return {
      mode: "offline",
      is_live: false,
      is_demo: false,
      public_demo_mode: true,
      backend: "not_configured",
      camera_active: false,
      tilt_risk: null,
      readiness: null,
      recovery: null,
      signal_confidence: 0,
      recommendation: "Backend is offline. Live camera still runs locally on /play.",
      updated_at: now
    };
  }
  if (path === "engine-health") {
    return {
      engine_connected: false,
      heartbeat_age_seconds: null,
      overlay_file_exists: false,
      overlay_file_valid: false,
      overlay_running: false,
      public_demo_mode: true
    };
  }
  if (path === "evidence") {
    return {
      public_session: {
        samples: 0,
        proof: {
          headline: "Demo proof available",
          details: "Use /demo for the scripted investor proof, or /play for local browser CV."
        }
      },
      public_demo_mode: true,
      backend: "not_configured",
      investor_metrics: { body_state_samples: 0, embedding_vectors: 0, feedback_labels: 0 },
      cohort: { total_alerts_labelled: 0, unique_testers: 0, tester_target: 100, validation_goal: 150, labels_target: 150 },
      cloud_coach: { provider: "fallback", status: "offline", latency_ms: null },
      public_timeline: { points: [], events: [] },
      hosted_storage: { configured: false, provider: "none" }
    };
  }
  if (path === "export-founder-deck" || path === "export-pitch-package") {
    return {
      ok: true,
      public_demo_mode: true,
      generated_at: now,
      files: ["client-side evidence package"],
      export_dir: "browser-download",
      raw_media_included: false
    };
  }
  if (path === "camera-check") {
    return {
      ok: false,
      public_demo_mode: true,
      error: "backend_not_configured",
      message: "Camera diagnostics are shown locally. Configure KAI_BACKEND_URL to persist camera failure cases."
    };
  }
  return { ok: false, error: "backend_not_configured", path };
};

module.exports = async function handler(req, res) {
  const parts = Array.isArray(req.query.path) ? req.query.path : [req.query.path].filter(Boolean);
  const path = parts.join("/");
  const backend = process.env.KAI_BACKEND_URL;

  if (!backend) {
    const status = req.method === "GET" ? 200 : 503;
    res.status(status).json(fallback(path));
    return;
  }

  const target = new URL(`/api/${path}`, backend);
  for (const [key, value] of Object.entries(req.query)) {
    if (key === "path") continue;
    if (Array.isArray(value)) value.forEach((item) => target.searchParams.append(key, item));
    else if (value != null) target.searchParams.set(key, value);
  }

  const headers = { ...req.headers };
  delete headers.host;
  delete headers["content-length"];
  if (process.env.KAI_WRITE_TOKEN && !headers["x-kai-token"]) {
    headers["x-kai-token"] = process.env.KAI_WRITE_TOKEN;
  }

  const init = { method: req.method, headers };
  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = typeof req.body === "string" ? req.body : JSON.stringify(req.body || {});
    init.headers["content-type"] = init.headers["content-type"] || "application/json";
  }

  try {
    const response = await fetch(target, init);
    const contentType = response.headers.get("content-type") || "application/json";
    res.status(response.status);
    res.setHeader("content-type", contentType);
    const body = await response.arrayBuffer();
    res.send(Buffer.from(body));
  } catch (error) {
    res.status(502).json({ ok: false, error: "backend_proxy_failed", detail: String(error?.message || error) });
  }
};
