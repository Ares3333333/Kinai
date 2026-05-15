// Lightweight English-only text helper for Kinaesthetic AI surfaces.
// The site is now prepared for the a16z-facing English demo. RU copy can be
// reintroduced later through a clean dictionary, without mojibake.

const dictionary = {
  en: {
    "play.title": "Kinaesthetic AI - live coach",
    "play.subtitle": "The camera sees your body. A cue arrives before you notice you are tilting.",
    "play.start": "Start",
    "play.stop": "Stop",
    "play.permit_camera": "Allow camera access",
    "play.calibration": "Calibration",
    "play.session_status": "Session status",
    "play.tilt_label": "Tension level",
    "play.alert_helped": "Helped",
    "play.alert_no_help": "Did not help",
    "play.alert_false": "False alert",
    "play.feedback_thanks": "Saved locally.",
    "play.engine_offline": "Engine is offline.",
    "play.mode_live": "live",
    "play.mode_stale": "stale",
    "play.mode_demo": "demo",
    "play.mode_offline": "offline",
  },
};

export function t(key, fallback = "") {
  return dictionary.en[key] || fallback || key;
}

export function applyTranslations(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((node) => {
    const key = node.getAttribute("data-i18n");
    node.textContent = t(key, node.textContent);
  });
}

window.KinaestheticI18n = { t, applyTranslations, lang: "en" };
