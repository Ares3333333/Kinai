// Render proof card as a 1080x1350 PNG in the browser.
// We never send raw frames anywhere — the canvas is generated locally and
// downloaded by the user. This becomes shareable proof on Discord/Twitter.

const W = 1080;
const H = 1350;

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export async function fetchProofPayload() {
  const response = await fetch("/api/proof-share", { cache: "no-store" });
  if (!response.ok) throw new Error("proof endpoint unavailable");
  return response.json();
}

export function renderProofToCanvas(proof) {
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");

  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, "#0c0d10");
  grad.addColorStop(1, "#04050a");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, W, H);

  ctx.fillStyle = "#35f29a";
  ctx.font = "bold 38px Inter, system-ui, sans-serif";
  ctx.fillText("KINAESTHETIC AI", 80, 130);

  ctx.fillStyle = "#a3abb6";
  ctx.font = "26px Inter, system-ui, sans-serif";
  ctx.fillText("real-time anti-tilt coach", 80, 175);

  ctx.fillStyle = "#f4f6f8";
  ctx.font = "bold 96px Inter, system-ui, sans-serif";
  const before = (proof.tilt_before ?? 0).toFixed(0);
  const after = (proof.tilt_after ?? 0).toFixed(0);
  ctx.fillText(`${before} → ${after}`, 80, 360);

  ctx.fillStyle = "#8ee8ff";
  ctx.font = "32px Inter, system-ui, sans-serif";
  const seconds = proof.recovery_seconds ? `${Number(proof.recovery_seconds).toFixed(1)}s recovery` : "in-session recovery";
  ctx.fillText(`tilt drop · ${seconds}`, 80, 420);

  ctx.fillStyle = "rgba(255,255,255,0.08)";
  roundRect(ctx, 80, 480, W - 160, 320, 24);
  ctx.fill();

  ctx.fillStyle = "#f4f6f8";
  ctx.font = "bold 36px Inter, system-ui, sans-serif";
  ctx.fillText(proof.command || "Soft jaw. Drop shoulders. Exhale.", 110, 560);

  ctx.fillStyle = "#a3abb6";
  ctx.font = "26px Inter, system-ui, sans-serif";
  ctx.fillText(proof.jaw || "Jaw stable", 110, 620);
  ctx.fillText(proof.shoulders || "Shoulders stable", 110, 660);
  ctx.fillText(`source: ${proof.source || "real"}${proof.is_synthetic ? " (synthetic)" : ""}`, 110, 700);

  ctx.fillStyle = "#6f7885";
  ctx.font = "22px Inter, system-ui, sans-serif";
  ctx.fillText(proof.watermark || "Kinaesthetic AI · localhost", 80, H - 130);
  ctx.fillText(proof.claim_disclaimer || "Performance coaching, not medical advice.", 80, H - 95);

  return canvas;
}

export async function saveProofPng(filenamePrefix = "kinaesthetic-proof") {
  const proof = await fetchProofPayload();
  if (!proof || proof.is_synthetic) {
    return { ok: false, reason: "synthetic" };
  }
  const canvas = renderProofToCanvas(proof);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob) return { ok: false, reason: "blob_failed" };
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  a.href = url;
  a.download = `${filenamePrefix}-${stamp}.png`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return { ok: true };
}
