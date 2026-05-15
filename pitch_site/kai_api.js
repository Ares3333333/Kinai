// Shared API helper for /play, /tester, /demo, /study, /admin.
//
// Why this exists: every POST to the backend must carry the write token
// (injected by the server into <head> as window.__KAI_WRITE_TOKEN__) and
// the consent version. Without these the backend returns 401 / 403. We
// also gracefully handle 429 by waiting the Retry-After window.
//
// Usage:
//     import { kaiPost, kaiGet, recordConsent } from "/kai_api.js";
//     await kaiPost("/api/feedback", { helped: true });

const TOKEN = (typeof window !== "undefined" && window.__KAI_WRITE_TOKEN__) || "";
const CONSENT_VERSION =
  (typeof window !== "undefined" && window.__KAI_CONSENT_VERSION__) || "v1";

function buildHeaders(extra = {}) {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (TOKEN) headers.set("X-Kai-Token", TOKEN);
  headers.set("X-Kai-Consent", CONSENT_VERSION);
  for (const [k, v] of Object.entries(extra)) {
    headers.set(k, v);
  }
  return headers;
}

async function readResponse(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { ok: false, error: "invalid_json", body: text };
  }
}

export async function kaiPost(path, body, options = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: buildHeaders(options.headers),
    body: JSON.stringify(body || {}),
    credentials: "same-origin",
    keepalive: Boolean(options.keepalive),
  });
  if (response.status === 429) {
    const retry = Number(response.headers.get("Retry-After") || 1);
    return { ok: false, error: "rate_limited", retry_after_seconds: retry };
  }
  return readResponse(response);
}

export function kaiBeacon(path, body) {
  const payload = JSON.stringify(body || {});
  try {
    return fetch(path, {
      method: "POST",
      headers: buildHeaders(),
      body: payload,
      credentials: "same-origin",
      keepalive: true,
    });
  } catch (_err) {
    return Promise.resolve({ ok: false });
  }
}

export async function kaiGet(path) {
  const response = await fetch(path, { credentials: "same-origin" });
  return readResponse(response);
}

// Tells the backend the user accepted the privacy notice. Idempotent --
// safe to call once per page-load. The backend appends to
// data/consent_log.jsonl for audit.
export async function recordConsent(payload = {}) {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (TOKEN) headers.set("X-Kai-Token", TOKEN);
  // /api/consent does NOT require X-Kai-Consent header (it BECOMES the
  // consent), so we omit it deliberately.
  const body = {
    accepted_at: new Date().toISOString(),
    consent_version: CONSENT_VERSION,
    lang: (document.documentElement && document.documentElement.lang) || "",
    tester_id:
      (typeof localStorage !== "undefined" && localStorage.getItem("kinaesthetic_tester_id")) ||
      "anonymous",
    ...payload,
  };
  try {
    const response = await fetch("/api/consent", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      credentials: "same-origin",
    });
    return await readResponse(response);
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export const KaiApi = { kaiGet, kaiPost, kaiBeacon, recordConsent, token: TOKEN };
