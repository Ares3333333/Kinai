# Kinaesthetic AI Desktop Shell

This folder is the serious Windows desktop path for the local beta.

It does not replace the public site. It starts the existing local Python
`site_server.py`, opens `/play` inside Electron, and opens `/overlay` in a
small always-on-top window.

## Why Electron for the beta

- Chromium camera permissions are closest to the working browser product.
- Web Speech / audio unlock behavior is easier to debug.
- Always-on-top overlay windows are mature.
- Packaging and Windows shortcuts are straightforward with electron-builder.

Tauri can still be revisited after the beta, once camera, overlay, and voice
behavior are stable with real testers.

## Development

From this folder:

```powershell
npm install
npm run dev
```

The shell expects the repository root two directories above this folder. You can
override it with:

```powershell
$env:KAI_PROJECT_ROOT="C:\Users\user\Desktop\MeirX"
npm run dev
```

## Runtime

- Local engine: `site_server.py`
- Local URL: `http://127.0.0.1:8765/play?desktop=1`
- Overlay URL: `http://127.0.0.1:8765/overlay?window=1&voice=1&desktop=1`
- Raw video/audio/frames are still not stored.
- Packaged builds copy `resources/kai-runtime` into Electron `userData/runtime`
  so `.venv`, logs, and derived data live in a writable Windows app folder.

## Beta milestones

1. Launch shell and overlay reliably from Electron.
2. Add first-run camera, audio, voice, and overlay checks.
3. Package Python engine as a sidecar executable.
4. Build signed NSIS installer.
5. Test on clean Windows machines.
