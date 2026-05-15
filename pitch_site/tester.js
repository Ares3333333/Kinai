import { kaiPost } from "/kai_api.js";
import { requireConsent } from "/consent.js";

document.getElementById("feedbackForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await requireConsent();
  const form = new FormData(event.currentTarget);
  const payload = {
    name: form.get("name") || "",
    game: form.get("game") || "",
    rounds: form.get("rounds") || "",
    helped_pct: form.get("helped_pct") || "",
    comment: form.get("comment") || "",
    source: "tester_page",
  };

  const status = document.getElementById("feedbackStatus");
  try {
    const result = await kaiPost("/api/feedback", payload);
    if (result && result.ok) {
      status.textContent = "Thanks. Saved to data/tester_feedback.jsonl.";
    } else if (result && result.error === "rate_limited") {
      status.textContent = `Rate-limited, try again in ${result.retry_after_seconds}s.`;
    } else {
      status.textContent = result?.error || "Failed to save feedback.";
    }
  } catch {
    status.textContent = "Feedback API unavailable. DM Arseniy directly.";
  }
});
