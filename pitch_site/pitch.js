async function loadPitchProof() {
  try {
    const response = await fetch("/api/proof-card", { cache: "no-store" });
    const proof = await response.json();
    document.getElementById("pitchProof").textContent = proof.headline || "Proof card появится после demo.";
    document.getElementById("pitchProofMeta").textContent = `${proof.jaw || "Jaw"} · ${proof.shoulders || "Shoulders"} · ${proof.command || "Recovery command"}`;
  } catch {
    document.getElementById("pitchProof").textContent = "Proof card появится после live session или pitch demo.";
  }
}

async function exportPitchDeck() {
  const status = document.getElementById("pitchExportStatus");
  status.textContent = "Собираю founder deck export...";
  try {
    const response = await fetch("/api/record-pitch-run", { cache: "no-store" });
    const result = await response.json();
    status.textContent = result.ok ? `Готово: ${result.export_dir}` : "Не удалось собрать export.";
  } catch {
    status.textContent = "Export API недоступен.";
  }
}

document.getElementById("exportPitchDeck")?.addEventListener("click", exportPitchDeck);
loadPitchProof();
