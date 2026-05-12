const q = (id) => document.getElementById(id);

function text(id, value) {
  const node = q(id);
  if (node) node.textContent = value ?? "—";
}

async function loadValidation() {
  try {
    const data = await fetch("/api/validation-mode", { cache: "no-store" }).then((response) => response.json());
    text("validationStatus", "ready");
    text("validationProgress", `${150 - Number(data.labels_remaining || 150)}/150 labels`);
    text("validationHeadline", `${data.session_samples || 0} сигналов · ${data.session_labels || 0} labels`);
    text("validationDetails", data.message || "Спасибо: эта сессия усиливает validation dataset.");
  } catch {
    text("validationStatus", "offline");
  }
}

loadValidation();
setInterval(loadValidation, 5000);
