const { app, BrowserWindow, Menu, ipcMain, shell, session } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");

const APP_PORT = Number(process.env.KAI_DESKTOP_PORT || 8765);
const APP_HOST = "127.0.0.1";
const APP_URL = `http://${APP_HOST}:${APP_PORT}`;
const PLAY_URL = `${APP_URL}/play?desktop=1`;
const OVERLAY_URL = `${APP_URL}/overlay?window=1&voice=1&desktop=1`;

let mainWindow = null;
let overlayWindow = null;
let playerWindow = null;
let engineProcess = null;
let engineStartedAt = null;
let bootStatus = "Starting Kinaesthetic AI...";

function copyFileIfExists(source, target) {
  if (!fs.existsSync(source)) {
    return;
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.copyFileSync(source, target);
}

function copyDirRecursive(source, target) {
  if (!fs.existsSync(source)) {
    return;
  }
  fs.mkdirSync(target, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    const sourcePath = path.join(source, entry.name);
    const targetPath = path.join(target, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(sourcePath, targetPath);
    } else if (entry.isFile()) {
      copyFileIfExists(sourcePath, targetPath);
    }
  }
}

function preparePackagedRuntime() {
  if (!app.isPackaged) {
    return;
  }
  const source = path.join(process.resourcesPath, "kai-runtime");
  const target = path.join(app.getPath("userData"), "runtime");
  if (!fs.existsSync(source)) {
    throw new Error(`Packaged runtime is missing: ${source}`);
  }

  const markerPath = path.join(target, ".kai-runtime-version");
  const currentVersion = app.getVersion();
  const existingVersion = fs.existsSync(markerPath) ? fs.readFileSync(markerPath, "utf8").trim() : "";
  if (existingVersion === currentVersion && fs.existsSync(path.join(target, "site_server.py"))) {
    return;
  }

  fs.mkdirSync(target, { recursive: true });
  for (const file of [
    "app.py",
    "alerts.py",
    "atomic_io.py",
    "coach_providers.py",
    "cv_engine.py",
    "engine_status.py",
    "force_utf8.py",
    "gemini_coach.py",
    "groq_coach.py",
    "product_intelligence.py",
    "scoring.py",
    "security.py",
    "site_server.py",
    "tts_phrases.py",
    "requirements-local.txt",
    ".env.example"
  ]) {
    copyFileIfExists(path.join(source, file), path.join(target, file));
  }
  copyDirRecursive(path.join(source, "pitch_site"), path.join(target, "pitch_site"));
  copyDirRecursive(path.join(source, "models"), path.join(target, "models"));
  fs.mkdirSync(path.join(target, "data"), { recursive: true });
  fs.writeFileSync(markerPath, currentVersion, "utf8");
}

function logsDir(root) {
  const dir = path.join(root, "logs");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function logLine(message) {
  const root = projectRoot();
  const line = `[${new Date().toISOString()}] ${message}\n`;
  try {
    fs.appendFileSync(path.join(logsDir(root), "desktop-electron.log"), line, "utf8");
  } catch {
    // Logging must never block app startup.
  }
}

function bootHtml(status, detail = "") {
  const safeStatus = String(status).replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));
  const safeDetail = String(detail).replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Kinaesthetic AI</title>
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: radial-gradient(circle at 20% 0%, #0d241c, #040706 56%);
      color: #f5f7f5;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(720px, calc(100vw - 48px));
      border: 1px solid rgba(51, 238, 153, .28);
      border-radius: 18px;
      padding: 34px;
      background: rgba(16, 19, 18, .84);
      box-shadow: 0 30px 80px rgba(0, 0, 0, .45);
    }
    .brand { color: #33ee99; font-weight: 900; letter-spacing: .08em; text-transform: uppercase; font-size: 13px; }
    h1 { margin: 14px 0 12px; font-size: 42px; line-height: 1.02; }
    p { margin: 0; color: #aeb8c1; font-size: 18px; line-height: 1.5; }
    .bar { height: 8px; margin-top: 28px; border-radius: 999px; overflow: hidden; background: rgba(255,255,255,.12); }
    .bar::before { content: ""; display: block; width: 42%; height: 100%; border-radius: inherit; background: #33ee99; animation: pulse 1.2s ease-in-out infinite alternate; }
    .detail { margin-top: 18px; font-size: 13px; color: #789; white-space: pre-wrap; }
    @keyframes pulse { from { transform: translateX(-20%); } to { transform: translateX(160%); } }
  </style>
</head>
<body>
  <main>
    <div class="brand">Kinaesthetic AI Desktop Beta</div>
    <h1>${safeStatus}</h1>
    <p>Starting the local coach engine, camera surface, and game overlay.</p>
    <div class="bar"></div>
    <div class="detail">${safeDetail}</div>
  </main>
</body>
</html>`;
}

async function showBoot(status, detail = "") {
  bootStatus = status;
  logLine(`${status}${detail ? ` - ${detail}` : ""}`);
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  const html = bootHtml(status, detail);
  await mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
}

function projectRoot() {
  if (process.env.KAI_PROJECT_ROOT) {
    return process.env.KAI_PROJECT_ROOT;
  }
  if (app.isPackaged) {
    return path.join(app.getPath("userData"), "runtime");
  }
  return path.resolve(__dirname, "..", "..");
}

function pythonCommand(root) {
  const venvPython = path.join(root, ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return process.env.PYTHON || "python";
}

function runCommand(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      ...options,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) {
        resolve({ stdout, stderr });
        return;
      }
      reject(new Error(`${command} ${args.join(" ")} failed with code ${code}\n${stderr || stdout}`));
    });
  });
}

async function createVenv(root) {
  const venvDir = path.join(root, ".venv");
  if (fs.existsSync(path.join(venvDir, "Scripts", "python.exe"))) {
    return;
  }
  const candidates = [
    { command: process.env.PYTHON, args: ["-m", "venv", ".venv"] },
    { command: "py", args: ["-3", "-m", "venv", ".venv"] },
    { command: "python", args: ["-m", "venv", ".venv"] },
    { command: "python3", args: ["-m", "venv", ".venv"] }
  ].filter((item) => item.command);

  let lastError = null;
  for (const candidate of candidates) {
    try {
      await runCommand(candidate.command, candidate.args, { cwd: root });
      return;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("Python was not found. Install Python 3.11+ and try again.");
}

async function ensureLocalRuntime(root) {
  preparePackagedRuntime();
  const requirementsPath = path.join(root, "requirements-local.txt");
  if (!fs.existsSync(requirementsPath)) {
    throw new Error(`Missing requirements-local.txt at ${requirementsPath}`);
  }

  await showBoot("Checking Python runtime", root);
  await createVenv(root);

  const python = pythonCommand(root);
  const markerPath = path.join(root, ".venv", ".kai-desktop-deps.json");
  const requirementsStat = fs.statSync(requirementsPath);
  let needsInstall = true;
  if (fs.existsSync(markerPath)) {
    try {
      const marker = JSON.parse(fs.readFileSync(markerPath, "utf8"));
      needsInstall = marker.requirements_mtime_ms !== requirementsStat.mtimeMs;
    } catch {
      needsInstall = true;
    }
  }

  if (!needsInstall) {
    await showBoot("Desktop runtime ready", "Python dependencies are already installed.");
    return;
  }

  await showBoot("Installing local CV dependencies", "This can take a few minutes on first launch.");
  await runCommand(python, ["-m", "pip", "install", "--upgrade", "pip", "--disable-pip-version-check"], { cwd: root });
  await runCommand(python, ["-m", "pip", "install", "-r", requirementsPath, "--disable-pip-version-check"], { cwd: root });
  fs.writeFileSync(
    markerPath,
    JSON.stringify({ requirements_mtime_ms: requirementsStat.mtimeMs, installed_at: new Date().toISOString() }, null, 2),
    "utf8"
  );
}

function waitForHttp(url, timeoutMs = 30000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(url, (res) => {
        res.resume();
        if (res.statusCode >= 200 && res.statusCode < 500) {
          resolve(true);
          return;
        }
        retry();
      });
      req.on("error", retry);
      req.setTimeout(1500, () => {
        req.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() - started > timeoutMs) {
        reject(new Error(`Local engine did not answer at ${url}`));
        return;
      }
      setTimeout(attempt, 500);
    };
    attempt();
  });
}

function startEngine() {
  if (engineProcess && !engineProcess.killed) {
    return;
  }

  const root = projectRoot();
  const serverPath = path.join(root, "site_server.py");
  if (!fs.existsSync(serverPath)) {
    throw new Error(`Cannot find site_server.py at ${serverPath}`);
  }

  engineStartedAt = Date.now();
  engineProcess = spawn(pythonCommand(root), [serverPath], {
    cwd: root,
    env: {
      ...process.env,
      HOST: APP_HOST,
      KAI_BIND_HOST: APP_HOST,
      PORT: String(APP_PORT),
      KAI_DESKTOP: "1",
      KAI_WRITE_AUTH: process.env.KAI_WRITE_AUTH || "off"
    },
    stdio: "ignore",
    windowsHide: true
  });

  engineProcess.on("exit", () => {
    logLine("Local engine exited.");
    engineProcess = null;
  });
}

function stopEngine() {
  if (engineProcess && !engineProcess.killed) {
    engineProcess.kill();
  }
  engineProcess = null;
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 980,
    minWidth: 1100,
    minHeight: 760,
    backgroundColor: "#050806",
    title: "Kinaesthetic AI",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function createOverlayWindow() {
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.show();
    overlayWindow.focus();
    return overlayWindow;
  }

  overlayWindow = new BrowserWindow({
    width: 520,
    height: 190,
    minWidth: 360,
    minHeight: 120,
    frame: false,
    transparent: true,
    resizable: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    backgroundColor: "#00000000",
    title: "Kinaesthetic AI Overlay",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  overlayWindow.setAlwaysOnTop(true, "screen-saver");
  overlayWindow.loadURL(OVERLAY_URL);
  overlayWindow.on("closed", () => {
    overlayWindow = null;
  });
  return overlayWindow;
}

function createPlayerWindow() {
  if (playerWindow && !playerWindow.isDestroyed()) {
    playerWindow.show();
    playerWindow.focus();
    return playerWindow;
  }

  playerWindow = new BrowserWindow({
    width: 1380,
    height: 920,
    minWidth: 1120,
    minHeight: 760,
    backgroundColor: "#050806",
    title: "Kinaesthetic AI - Live Player",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  playerWindow.loadURL(PLAY_URL);
  playerWindow.on("closed", () => {
    playerWindow = null;
  });
  return playerWindow;
}

function createMenu() {
  const template = [
    {
      label: "Kinaesthetic AI",
      submenu: [
        { label: "Open player mode", click: () => createPlayerWindow() },
        { label: "Open overlay", click: () => createOverlayWindow() },
        { label: "Restart local engine", click: async () => restartEngine() },
        { type: "separator" },
        { label: "Quit", role: "quit" }
      ]
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { role: "togglefullscreen" }
      ]
    }
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function restartEngine() {
  stopEngine();
  await ensureLocalRuntime(projectRoot());
  startEngine();
  await waitForHttp(`${APP_URL}/health`);
  if (mainWindow && !mainWindow.isDestroyed()) {
    await mainWindow.loadFile(path.join(__dirname, "app.html"));
  }
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    await overlayWindow.loadURL(OVERLAY_URL);
  }
  if (playerWindow && !playerWindow.isDestroyed()) {
    await playerWindow.loadURL(PLAY_URL);
  }
  return { ok: true };
}

function installIpc() {
  ipcMain.handle("desktop:urls", () => ({
    app: APP_URL,
    play: PLAY_URL,
    overlay: OVERLAY_URL,
    demo: `${APP_URL}/demo`,
    evidence: `${APP_URL}/evidence`,
    metrics: `${APP_URL}/metrics`,
    cameraCheck: `${APP_URL}/camera-check`
  }));
  ipcMain.handle("overlay:open", () => {
    createOverlayWindow();
    return { ok: true };
  });
  ipcMain.handle("overlay:close", () => {
    overlayWindow?.close();
    return { ok: true };
  });
  ipcMain.handle("player:open", () => {
    createPlayerWindow();
    return { ok: true };
  });
  ipcMain.handle("route:open", (_event, route) => {
    const safeRoute = String(route || "/").startsWith("/") ? String(route || "/") : "/";
    const url = `${APP_URL}${safeRoute}`;
    const win = new BrowserWindow({
      width: 1180,
      height: 820,
      minWidth: 960,
      minHeight: 680,
      backgroundColor: "#050806",
      title: `Kinaesthetic AI ${safeRoute}`,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false
      }
    });
    win.loadURL(url);
    return { ok: true };
  });
  ipcMain.handle("engine:restart", restartEngine);
  ipcMain.handle("engine:status", () => ({
    ok: true,
    running: Boolean(engineProcess && !engineProcess.killed),
    port: APP_PORT,
    uptime_seconds: engineStartedAt ? Math.round((Date.now() - engineStartedAt) / 1000) : 0
  }));
}

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(permission === "media");
  });

  app.setAppUserModelId("com.sharprod.kinaestheticai");
  createMenu();
  installIpc();
  createMainWindow();
  await showBoot(bootStatus);

  try {
    await ensureLocalRuntime(projectRoot());
    await showBoot("Starting local coach engine", `${APP_URL}/play`);
    startEngine();
    await waitForHttp(`${APP_URL}/health`);
    await showBoot("Opening desktop command center", "Live camera opens from the Player module.");
    await mainWindow.loadFile(path.join(__dirname, "app.html"));
    createOverlayWindow();
  } catch (error) {
    await showBoot("Local engine failed to start", error.message);
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
});

app.on("window-all-closed", () => {
  stopEngine();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", stopEngine);
