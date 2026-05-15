(function () {
  "use strict";

  function setStatus(state, text) {
    var pill = document.getElementById("landingStatus");
    var label = document.getElementById("landingStatusText");
    var help = document.getElementById("landingStatusHelp");
    if (!pill || !label) return;
    pill.dataset.state = state;
    label.textContent = text;
    if (help) help.hidden = state === "ok";
  }

  function setRuntimeStatus(mode, context) {
    var normalized = String(mode || "offline").toLowerCase();
    var isPublicDemo = Boolean(context && (context.public_demo_mode || context.backend === "not_configured"));
    if (isPublicDemo) {
      setStatus("warn", "Public demo mode. Investor demo works; live camera runs locally in the browser.");
      return;
    }
    if (normalized === "live") {
      setStatus("ok", "Live engine connected. Ready.");
      return;
    }
    if (normalized === "demo" || normalized === "stale") {
      setStatus("warn", "Engine is up, but live session is not running yet.");
      return;
    }
    setStatus("error", "Live engine is offline on this machine.");
  }

  function pollEngine() {
    Promise.all([
      fetch("/api/system-health", { cache: "no-store" }),
      fetch("/api/engine-health", { cache: "no-store" }),
    ])
      .then(function (responses) {
        var healthResponse = responses[0];
        var engineResponse = responses[1];
        return Promise.all([
          healthResponse && healthResponse.ok ? healthResponse.json() : null,
          engineResponse && engineResponse.ok ? engineResponse.json() : null,
        ]);
      })
      .then(function (payload) {
        var health = payload[0];
        var engine = payload[1];
        if (health && health.mode) {
          setRuntimeStatus(health.mode, health);
          return;
        }
        if (engine && engine.engine_connected && engine.overlay_running) {
          setRuntimeStatus("live", engine);
          return;
        }
        if (engine && engine.engine_connected) {
          setRuntimeStatus("stale", engine);
          return;
        }
        setRuntimeStatus("offline", engine);
      })
      .catch(function () {
        setRuntimeStatus("offline");
      });
  }

  function setupHelpDialog() {
    var dialog = document.getElementById("engineHelpDialog");
    var helpButton = document.getElementById("landingStatusHelp");
    if (!dialog || !helpButton) return;
    helpButton.addEventListener("click", function () {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "open");
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupHelpDialog();
    pollEngine();
    setInterval(pollEngine, 4000);
  });
})();
