const q = (id) => document.getElementById(id);

function text(id, value) {
  const node = q(id);
  if (node) node.textContent = value ?? "—";
}

function pct(value) {
  return `${Math.round(Number(value) || 0)}%`;
}

async function runPitchDemo() {
  const script = await fetch("/api/pitch-demo-script", { cache: "no-store" }).then((response) => response.json());
  const phases = script.phases || [];
  for (const phase of phases) {
    text("phaseLabel", phase.id);
    text("phaseTitle", phase.title);
    text("phaseCommand", phase.command);
    text("phaseTilt", pct(phase.tilt_risk));
    text("phaseReadiness", pct(phase.readiness));
    text("phaseRecovery", pct(phase.recovery));
    await new Promise((resolve) => setTimeout(resolve, Math.min(phase.seconds * 1000, 6000)));
  }
  await fetch("/api/export-pitch-package", { cache: "no-store" }).catch(() => {});
  text("phaseLabel", "export");
  text("phaseTitle", "Pitch package готов");
  text("phaseCommand", "Evidence, proof, privacy и metrics экспортированы.");
}

q("runPitchDemo")?.addEventListener("click", runPitchDemo);
