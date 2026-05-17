const desktop = window.kinaestheticDesktop;

const views = [...document.querySelectorAll(".view")];
const navItems = [...document.querySelectorAll(".nav-item")];

function selectView(name) {
  for (const item of navItems) {
    item.classList.toggle("active", item.dataset.view === name);
  }
  for (const view of views) {
    view.classList.toggle("active", view.id === `view-${name}`);
  }
}

for (const item of navItems) {
  item.addEventListener("click", () => selectView(item.dataset.view));
}

document.querySelector("#openPlayerBtn")?.addEventListener("click", () => desktop?.openPlayer());
document.querySelector("#openOverlayBtn")?.addEventListener("click", () => desktop?.openOverlay());
document.querySelector("#restartEngineBtn")?.addEventListener("click", async () => {
  const target = document.querySelector("#settingsEngine");
  if (target) target.textContent = "Restarting local engine...";
  await desktop?.restartEngine();
  if (target) target.textContent = "Local engine restarted.";
  refreshEngineStatus();
});

document.addEventListener("click", (event) => {
  const routeButton = event.target.closest("[data-route]");
  if (routeButton) {
    desktop?.openRoute(routeButton.dataset.route);
  }
  const overlayButton = event.target.closest("[data-action='overlay']");
  if (overlayButton) {
    desktop?.openOverlay();
  }
});

async function refreshEngineStatus() {
  const status = await desktop?.engineStatus?.();
  const engineStatus = document.querySelector("#engineStatus");
  const settingsEngine = document.querySelector("#settingsEngine");
  if (!status) return;
  const text = status.running ? `Engine: local ${status.uptime_seconds}s` : "Engine: offline";
  if (engineStatus) engineStatus.textContent = text;
  if (settingsEngine) settingsEngine.textContent = status.running ? "Local engine is running." : "Local engine is offline.";
}

refreshEngineStatus();
setInterval(refreshEngineStatus, 3000);
