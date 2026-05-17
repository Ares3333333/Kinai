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
let engineProcess = null;
let engineStartedAt = null;

function projectRoot() {
  if (process.env.KAI_PROJECT_ROOT) {
    return process.env.KAI_PROJECT_ROOT;
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

function createMenu() {
  const template = [
    {
      label: "Kinaesthetic AI",
      submenu: [
        { label: "Open player mode", click: () => mainWindow?.loadURL(PLAY_URL) },
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
  startEngine();
  await waitForHttp(`${APP_URL}/health`);
  if (mainWindow && !mainWindow.isDestroyed()) {
    await mainWindow.loadURL(PLAY_URL);
  }
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    await overlayWindow.loadURL(OVERLAY_URL);
  }
  return { ok: true };
}

function installIpc() {
  ipcMain.handle("overlay:open", () => {
    createOverlayWindow();
    return { ok: true };
  });
  ipcMain.handle("overlay:close", () => {
    overlayWindow?.close();
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

  try {
    startEngine();
    await waitForHttp(`${APP_URL}/health`);
    await mainWindow.loadURL(PLAY_URL);
    createOverlayWindow();
  } catch (error) {
    const message = encodeURIComponent(
      `Local engine failed to start. Check Python/.venv and requirements-local.txt.\n\n${error.message}`
    );
    await mainWindow.loadURL(`data:text/html;charset=utf-8,<pre style="font:16px sans-serif;white-space:pre-wrap;padding:32px">${message}</pre>`);
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
