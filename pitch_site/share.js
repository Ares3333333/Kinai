const q = (id) => document.getElementById(id);
const params = new URLSearchParams(window.location.search);

function setText(id, value) {
  const node = q(id);
  if (node) node.textContent = value ?? "—";
}

async function loadShareProof() {
  const id = params.get("id") || "";
  const url = id ? `/api/share-proof?id=${encodeURIComponent(id)}` : "/api/share-proof";
  try {
    const proof = await fetch(url, { cache: "no-store" }).then((response) => response.json());
    const stats = proof.stats || {};
    setText("shareHeadline", proof.headline || "Tilt — → —");
    setText("shareSubline", proof.subline || "Proof ещё не собран.");
    setText("shareRecovery", stats.recovery_seconds ? `${stats.recovery_seconds} сек` : "—");
    setText("shareJaw", stats.jaw || "—");
    setText("shareShoulders", stats.shoulders || "—");
    setText("shareCommand", stats.command_worked ? "Command worked" : (stats.command || "Command pending"));
  } catch {
    setText("shareSubline", "Share proof API offline.");
  }
}

loadShareProof();
