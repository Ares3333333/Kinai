// First-run privacy consent popup.
// We never silently start the camera or write derived data without an
// explicit accept. Decision is stored locally so testers see it once,
// AND a server-side audit log entry is appended via /api/consent.

import { t, applyTranslations } from "/i18n.js";
import { recordConsent } from "/kai_api.js";

const CONSENT_KEY = "kinaesthetic_consent_v1";

export function hasConsent() {
  const value = localStorage.getItem(CONSENT_KEY);
  if (value === "accepted") return true;
  try {
    return Boolean(JSON.parse(value || "null")?.accepted_at);
  } catch {
    return false;
  }
}

export function clearConsent() {
  localStorage.removeItem(CONSENT_KEY);
}

export function requireConsent() {
  return new Promise((resolve) => {
    if (hasConsent()) {
      resolve(true);
      return;
    }

    const overlay = document.createElement("div");
    overlay.className = "consent-overlay";
    overlay.innerHTML = `
      <div class="consent-panel" role="dialog" aria-modal="true">
        <h2 data-i18n="play.consent_title">${t("play.consent_title")}</h2>
        <p data-i18n="play.consent_body">${t("play.consent_body")}</p>
        <ul class="consent-list">
          <li>data/overlay_state.json - derived metrics</li>
          <li>data/affective_somatic_dataset.jsonl - body-state samples</li>
          <li>data/body_state_embeddings.jsonl - body-state vectors</li>
          <li>data/tester_feedback.jsonl - your responses</li>
        </ul>
        <div class="consent-actions">
          <button class="primary" id="consentAccept" data-i18n="play.consent_accept">${t("play.consent_accept")}</button>
          <button class="ghost" id="consentDecline" data-i18n="play.consent_decline">${t("play.consent_decline")}</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    applyTranslations(overlay);
    overlay.querySelector("#consentAccept").addEventListener("click", () => {
      localStorage.setItem(CONSENT_KEY, "accepted");
      overlay.remove();
      // Fire-and-forget server log; we don't block the user on the
      // network call, but if it fails we keep the local accept.
      recordConsent({ source: "consent_dialog" }).catch(() => {});
      resolve(true);
    });
    overlay.querySelector("#consentDecline").addEventListener("click", () => {
      localStorage.setItem(CONSENT_KEY, "declined");
      overlay.remove();
      resolve(false);
    });
  });
}
