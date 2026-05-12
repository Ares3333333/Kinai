import { kaiPost } from "/kai_api.js";

const q = (id) => document.getElementById(id);
const params = new URLSearchParams(window.location.search);
const testerId = params.get("tester") || localStorage.getItem("kinaesthetic_tester_id") || "anonymous";
const game = params.get("game") || localStorage.getItem("kinaesthetic_game") || "";
let activeStream = null;

function setText(id, value) {
  const node = q(id);
  if (node) node.textContent = value;
}

function setBadge(id, value, state = "") {
  const node = q(id);
  if (!node) return;
  node.textContent = value;
  if (state) node.dataset.state = state;
}

async function permissionState() {
  if (!navigator.permissions?.query) return "unknown";
  try {
    const status = await navigator.permissions.query({ name: "camera" });
    return status?.state || "unknown";
  } catch {
    return "unknown";
  }
}

async function countDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return { video: 0, audio: 0 };
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return {
      video: devices.filter((item) => item.kind === "videoinput").length,
      audio: devices.filter((item) => item.kind === "audioinput").length,
    };
  } catch {
    return { video: 0, audio: 0 };
  }
}

function classify(error) {
  const name = String(error?.name || "");
  if (name === "NotAllowedError" || name === "SecurityError") return "blocked";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "not_found";
  if (name === "NotReadableError" || name === "AbortError") return "busy";
  if (name === "TimeoutError") return "timeout";
  return "error";
}

function resultCopy(result) {
  const map = {
    ready: ["Камера готова", "Можно запускать player mode."],
    blocked: ["Камера заблокирована", "Разрешите Camera -> Allow в адресной строке и обновите страницу."],
    not_found: ["Камера не найдена", "Подключите веб-камеру или выберите ее в настройках браузера."],
    busy: ["Камера занята", "Закройте Zoom, Discord, OBS или другое приложение с камерой."],
    timeout: ["Браузер ждет permission", "Найдите popup/иконку камеры и выберите Allow."],
    https_required: ["Нужен HTTPS", "Публичная ссылка должна быть https://. Локально используйте localhost."],
    unsupported: ["Браузер не поддерживает камеру", "Откройте сайт в Chrome или Edge."],
    error: ["Камера не стартовала", "Проверьте permission, HTTPS и занятые приложения."],
  };
  return map[result] || map.error;
}

async function logCameraCheck(result, diagnostics) {
  const payload = {
    source: "camera_check",
    result,
    tester_id: testerId,
    game,
    diagnostics: {
      ...diagnostics,
      is_secure_context: window.isSecureContext,
      user_agent: navigator.userAgent,
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      page: location.pathname,
    },
  };
  const response = await kaiPost("/api/camera-check", payload).catch((error) => ({ ok: false, error: String(error) }));
  setBadge("saveBadge", response?.ok ? "failure cases: logged" : "failure cases: local only", response?.ok ? "live" : "stale");
}

async function runCheck() {
  const button = q("runCameraCheck");
  const video = q("cameraCheckPreview");
  const placeholder = q("cameraCheckPlaceholder");
  if (activeStream) {
    activeStream.getTracks().forEach((track) => track.stop());
    activeStream = null;
  }
  if (button) button.disabled = true;
  setText("checkResult", "Проверяем...");
  setText("checkDetails", "Браузер может показать permission popup.");
  setText("nextStep", "Ждем");
  setText("nextStepDetails", "Выберите Allow, если появится запрос.");

  const secure = window.isSecureContext || ["localhost", "127.0.0.1"].includes(location.hostname);
  const perm = await permissionState();
  const beforeDevices = await countDevices();
  setBadge("secureBadge", secure ? "secure: ok" : "secure: no https", secure ? "live" : "stale");
  setBadge("permissionBadge", `camera: ${perm}`, perm === "granted" ? "live" : perm === "denied" ? "stale" : "");

  if (!secure) {
    const result = "https_required";
    const [headline, details] = resultCopy(result);
    setText("checkResult", headline);
    setText("checkDetails", details);
    setText("nextStep", "Откройте HTTPS");
    setText("nextStepDetails", "Для публичных тестеров нужна Vercel/production https-ссылка.");
    await logCameraCheck(result, { permission_state: perm, media_devices_supported: Boolean(navigator.mediaDevices), ...beforeDevices });
    if (button) button.disabled = false;
    return;
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    const result = "unsupported";
    const [headline, details] = resultCopy(result);
    setText("checkResult", headline);
    setText("checkDetails", details);
    await logCameraCheck(result, { permission_state: perm, media_devices_supported: false, ...beforeDevices });
    if (button) button.disabled = false;
    return;
  }

  try {
    const timeout = new Promise((_, reject) => {
      setTimeout(() => {
        const error = new Error("camera_check_timeout");
        error.name = "TimeoutError";
        reject(error);
      }, 20000);
    });
    activeStream = await Promise.race([
      navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" }, audio: false }),
      timeout,
    ]);
    if (video) {
      video.srcObject = activeStream;
      await video.play().catch(() => {});
    }
    if (placeholder) placeholder.style.display = "none";
    const afterDevices = await countDevices();
    setText("checkResult", "Камера готова");
    setText("checkDetails", "Preview работает. Теперь можно запускать live coach.");
    setText("deviceCount", `${afterDevices.video} camera`);
    setText("deviceDetails", `${afterDevices.audio} audio inputs found, but audio is not used.`);
    setText("nextStep", "Start /play");
    setText("nextStepDetails", "Откройте player mode и нажмите «Старт с камерой».");
    setBadge("permissionBadge", "camera: granted", "live");
    await logCameraCheck("ready", { permission_state: "granted", media_devices_supported: true, video_input_count: afterDevices.video, audio_input_count: afterDevices.audio });
  } catch (error) {
    const result = classify(error);
    const [headline, details] = resultCopy(result);
    setText("checkResult", headline);
    setText("checkDetails", details);
    setText("deviceCount", `${beforeDevices.video} camera`);
    setText("deviceDetails", "Device labels may stay hidden until camera permission is granted.");
    setText("nextStep", result === "blocked" ? "Allow camera" : "Fix setup");
    setText("nextStepDetails", details);
    if (placeholder) {
      placeholder.style.display = "grid";
      placeholder.textContent = details;
    }
    await logCameraCheck(result, {
      permission_state: await permissionState(),
      media_devices_supported: true,
      video_input_count: beforeDevices.video,
      audio_input_count: beforeDevices.audio,
      error_name: error?.name || "",
      error_message: error?.message || "",
    });
  } finally {
    if (button) button.disabled = false;
  }
}

q("runCameraCheck")?.addEventListener("click", runCheck);

permissionState().then((state) => {
  setBadge("permissionBadge", `camera: ${state}`, state === "granted" ? "live" : state === "denied" ? "stale" : "");
});
setBadge("secureBadge", window.isSecureContext ? "secure: ok" : "secure: check", window.isSecureContext ? "live" : "");
countDevices().then((devices) => {
  setText("deviceCount", `${devices.video} camera`);
  setText("deviceDetails", devices.video ? "Camera device found." : "Camera may appear after permission.");
});
