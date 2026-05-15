import { kaiPost } from "/kai_api.js";

const $ = (id) => document.getElementById(id);
const fmt = (value) => new Intl.NumberFormat("ru-RU").format(Number(value) || 0);

function formPayload(form) {
  return Object.fromEntries(new FormData(form).entries());
}

async function postStudy(event, extra = {}) {
  const base = formPayload($("studyForm"));
  const payload = { ...base, ...extra, event };
  const result = await kaiPost("/api/study-event", payload);
  $("studyStatus").textContent = result?.ok ? `Saved: ${event}` : "Could not save event.";
  loadSummary();
}

async function postLabel(label) {
  await postStudy("label", { [label]: true, label });
  await kaiPost("/api/feedback", { ...formPayload($("studyForm")), [label]: true, source: "study_page", moment: "study_hotkey" });
}

async function loadSummary() {
  const summary = await fetch("/api/validation-study-summary", { cache: "no-store" }).then((r) => r.json());
  $("studyEvents").textContent = fmt(summary.events);
  $("studyTesters").textContent = fmt(summary.unique_testers);
  $("studyLabels").textContent = fmt(summary.cohort?.total_alerts_labelled);
  $("studyRemaining").textContent = fmt(summary.cohort?.labels_remaining);
  const recent = summary.recent || [];
  $("studyRecent").innerHTML = recent.length
    ? recent.map((row) => `<li><strong>${row.event}</strong><span>${row.tester_id || "tester"} - ${row.game || ""}</span></li>`).join("")
    : "<li>No study events yet.</li>";
}

$("startStudy")?.addEventListener("click", () => postStudy("session_start"));
$("endStudy")?.addEventListener("click", () => postStudy("session_end"));
document.querySelectorAll("[data-label]").forEach((button) => {
  button.addEventListener("click", () => postLabel(button.dataset.label));
});
$("postStudyForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await postStudy("post_session_feedback", formPayload(event.currentTarget));
});
window.addEventListener("keydown", (event) => {
  const key = event.key.toLowerCase();
  if (key === "t") postLabel("felt_tension");
  if (key === "h") postLabel("helped");
  if (key === "f") postLabel("false_alert");
});
loadSummary().catch(() => {
  $("studyStatus").textContent = "Study API unavailable.";
});
