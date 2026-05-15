async function loadPitchProof() {
  try {
    const response = await fetch("/api/proof-card", { cache: "no-store" });
    const proof = await response.json();
    document.getElementById("pitchProof").textContent = proof.headline || "Proof card appears after the demo.";
    document.getElementById("pitchProofMeta").textContent = `${proof.jaw || "Jaw"} - ${proof.shoulders || "Shoulders"} - ${proof.command || "Recovery command"}`;
  } catch {
    document.getElementById("pitchProof").textContent = "Proof card appears after a live session or pitch demo.";
  }
}

async function exportPitchDeck() {
  const status = document.getElementById("pitchExportStatus");
  status.textContent = "Building founder deck export...";
  try {
    const response = await fetch("/api/record-pitch-run", { cache: "no-store" });
    const result = await response.json();
    status.textContent = result.ok ? `Ready: ${result.export_dir}` : "Could not build export.";
  } catch {
    status.textContent = "Export API unavailable.";
  }
}

document.getElementById("exportPitchDeck")?.addEventListener("click", exportPitchDeck);
loadPitchProof();
