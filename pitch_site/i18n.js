// Lightweight i18n (RU/EN) for Kinaesthetic AI surfaces.
// No build step, no bundler. Pages opt in by including this script and
// adding data-i18n="key" to elements they want translated.

const dictionary = {
  ru: {
    "play.title": "Kinaesthetic AI · live coach",
    "play.subtitle": "Камера видит тело. Команда приходит до того, как ты сам заметил тильт.",
    "play.start": "Начать",
    "play.stop": "Остановить",
    "play.permit_camera": "Разреши доступ к камере",
    "play.calibration": "Калибровка",
    "play.calibration_neutral": "Сядь нейтрально на 5 секунд",
    "play.session_status": "Статус сессии",
    "play.tilt_label": "Уровень напряжения",
    "play.tilt_low": "низкий",
    "play.tilt_medium": "средний",
    "play.tilt_high": "высокий",
    "play.alert_helped": "Помогло",
    "play.alert_no_help": "Не помогло",
    "play.alert_false": "Ложная тревога",
    "play.feedback_thanks": "Спасибо. Сохранено локально.",
    "play.mute_on": "Голос включён",
    "play.mute_off": "Голос выключен (Ctrl+Alt+M)",
    "play.share_proof": "Сохранить proof card как PNG",
    "play.share_saved": "Proof card сохранён",
    "play.share_unavailable": "Proof пока нет — нужен пик и recovery",
    "play.consent_title": "Что мы пишем",
    "play.consent_body": "Мы не сохраняем видео и звук. Мы пишем только производные сигналы (плечи, челюсть, поза) и твои ответы. Данные хранятся локально на этом компьютере. Удалить можно одним кликом — папка data/.",
    "play.consent_accept": "Согласен · продолжить",
    "play.consent_decline": "Не согласен",
    "play.confidence_low": "Слабый сигнал. Поправь свет или сядь ближе к камере.",
    "play.engine_offline": "Кокпит не запущен.",
    "play.engine_offline_long": "Кокпит не запущен. Двойной клик по верхнему ярлыку Kinaesthetic AI.",
    "play.engine_banner_title": "Кокпит на этом ПК не запущен.",
    "play.engine_banner_body": "Запусти кокпит один раз — и эта страница начнёт видеть твоё тело в реальном времени.",
    "play.engine_step_1": "Двойной клик по верхнему ярлыку Kinaesthetic AI на рабочем столе.",
    "play.engine_step_2": "Подожди ~10 секунд, кокпит откроется на порту 8501.",
    "play.engine_step_3": "Возвращайся сюда — этот баннер сам исчезнет.",
    "play.tilt_offline": "нет сигнала",
    "play.tilt_suffix": "/ 100",
    "play.tilt_help": "0–40 спокойно · 40–70 нарастает · 70+ тильт. Считается из позы, челюсти и эмоций в реальном времени.",
    "play.live_running": "LIVE · коуч смотрит за тобой.",
    "play.command_meta_alert": "Действуй за один вдох",
    "play.command_meta_recovery": "Восстановление",
    "play.command_recovery": "Сохраняй мягкость",
    "play.command_calm": "Всё под контролем",
    "play.command_meta_calm": "Действий не требуется",
    "play.command_stale": "Кокпит на паузе",
    "play.command_offline": "Запусти кокпит",
    "play.feedback_q": "Помогла команда?",
    "play.mode_live": "live",
    "play.mode_stale": "stale",
    "play.mode_demo": "demo",
    "play.mode_offline": "offline",
    "play.mini_fps": "FPS кокпита",
    "play.mini_signal": "Сигнал камеры",
    "play.mini_mode": "Режим engine",
    "play.mini_labels": "Labels собрано",
    "play.hotkeys_title": "Горячие клавиши (работают пока вкладка в фокусе):",
    "play.hotkeys_mute": "выключить голос коуча",
    "play.stale": "Live engine не обновляется. Старые числа не показываем.",
    "play.lang": "EN",
  },
  en: {
    "play.title": "Kinaesthetic AI · live coach",
    "play.subtitle": "The camera sees your body. A cue arrives before you notice you're tilting.",
    "play.start": "Start",
    "play.stop": "Stop",
    "play.permit_camera": "Allow camera access",
    "play.calibration": "Calibration",
    "play.calibration_neutral": "Sit neutral for 5 seconds",
    "play.session_status": "Session status",
    "play.tilt_label": "Tension level",
    "play.tilt_low": "low",
    "play.tilt_medium": "medium",
    "play.tilt_high": "high",
    "play.alert_helped": "Helped",
    "play.alert_no_help": "Didn't help",
    "play.alert_false": "False alert",
    "play.feedback_thanks": "Thanks. Stored locally.",
    "play.mute_on": "Voice on",
    "play.mute_off": "Voice muted (Ctrl+Alt+M)",
    "play.share_proof": "Save proof card as PNG",
    "play.share_saved": "Proof card saved",
    "play.share_unavailable": "No proof yet — need a peak and a recovery",
    "play.consent_title": "What we record",
    "play.consent_body": "We do not save video or audio. We write only derived signals (shoulders, jaw, posture) and your responses. Data lives locally on this machine. Delete with one click — the data/ folder.",
    "play.consent_accept": "Accept · continue",
    "play.consent_decline": "Decline",
    "play.confidence_low": "Weak signal. Improve lighting or move closer to the camera.",
    "play.engine_offline": "Cockpit is not running.",
    "play.engine_offline_long": "Cockpit is not running. Double-click the top desktop shortcut Kinaesthetic AI.",
    "play.engine_banner_title": "The cockpit on this PC is not running.",
    "play.engine_banner_body": "Open the cockpit once — then this page will see your body in real time.",
    "play.engine_step_1": "Double-click the desktop shortcut Kinaesthetic AI (the top one).",
    "play.engine_step_2": "Wait ~10 seconds until the cockpit opens on port 8501.",
    "play.engine_step_3": "Come back here — this banner will disappear automatically.",
    "play.tilt_offline": "no signal",
    "play.tilt_suffix": "/ 100",
    "play.tilt_help": "0–40 calm · 40–70 building · 70+ tilted. Computed from posture, jaw and emotion in real time.",
    "play.live_running": "LIVE · the coach is watching.",
    "play.command_meta_alert": "Act in one breath",
    "play.command_meta_recovery": "Recovery window",
    "play.command_recovery": "Stay soft",
    "play.command_calm": "Riding green",
    "play.command_meta_calm": "No action needed",
    "play.command_stale": "Engine paused",
    "play.command_offline": "Start the cockpit",
    "play.feedback_q": "Did this command help?",
    "play.mode_live": "live",
    "play.mode_stale": "stale",
    "play.mode_demo": "demo",
    "play.mode_offline": "offline",
    "play.mini_fps": "Cockpit FPS",
    "play.mini_signal": "Camera signal",
    "play.mini_mode": "Engine mode",
    "play.mini_labels": "Labels collected",
    "play.hotkeys_title": "Hotkeys (work in any app while this tab is focused):",
    "play.hotkeys_mute": "mute coach voice",
    "play.stale": "Live engine not updating. Hiding stale numbers.",
    "play.lang": "RU",
  },
};

const I18N_KEY = "kinaesthetic_lang_v1";

export function currentLang() {
  const stored = localStorage.getItem(I18N_KEY);
  if (stored === "ru" || stored === "en") return stored;
  return navigator.language && navigator.language.toLowerCase().startsWith("ru") ? "ru" : "en";
}

export function setLang(lang) {
  localStorage.setItem(I18N_KEY, lang === "ru" ? "ru" : "en");
  applyTranslations();
}

export function t(key, lang) {
  const useLang = lang || currentLang();
  return (dictionary[useLang] && dictionary[useLang][key]) || dictionary.en[key] || key;
}

export function applyTranslations(root) {
  const scope = root || document;
  const lang = currentLang();
  scope.querySelectorAll("[data-i18n]").forEach((node) => {
    const key = node.getAttribute("data-i18n");
    node.textContent = t(key, lang);
  });
  scope.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    const key = node.getAttribute("data-i18n-placeholder");
    node.setAttribute("placeholder", t(key, lang));
  });
  document.documentElement.lang = lang;
}

export function toggleLang() {
  setLang(currentLang() === "ru" ? "en" : "ru");
}

if (typeof window !== "undefined") {
  window.kinaesthetic_i18n = { t, currentLang, setLang, toggleLang, applyTranslations };
}
