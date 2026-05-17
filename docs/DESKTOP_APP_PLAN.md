# Kinaesthetic AI Desktop App Plan

## Decision

Yes: Kinaesthetic AI should become a downloadable Windows app.

The best path is staged:

1. **Now: portable Windows app**
   - ship a zip/folder;
   - user runs `Start-Game-Overlay.bat`;
   - first launch creates `.venv`, installs `requirements-local.txt`, starts the local server, opens `/play`, and opens the tiny overlay;
   - raw video/audio/frames stay local.

2. **Now starting: Electron desktop shell**
   - one polished app window for `/play`;
   - one small always-on-top overlay window for gameplay;
   - local Python `site_server.py` runs as the desktop engine;
   - camera and voice stay in Chromium, close to the already-working browser path.

3. **Next: signed installer**
   - package the Python engine as a sidecar executable;
   - Start Menu/Desktop shortcuts;
   - first-run camera, voice, and overlay setup;
   - optional auto-update later.

4. **Later: Tauri evaluation**
   - revisit Tauri when beta behavior is stable;
   - use it only if smaller binary size matters more than Chromium camera reliability.

## Why not rewrite now

The current local stack already works:

- `app.py` is the Streamlit research cockpit.
- `site_server.py` serves `/play`, `/overlay`, `/demo`, `/evidence`, and local APIs.
- Browser CV is already reliable in Edge/Chrome.
- The overlay already works as a small topmost browser-app window.

Rewriting into native UI before packaging would risk camera permission bugs and delay validation.

## Recommended user-facing launchers

- `Start-Desktop-App.bat`
  Desktop beta shell. Opens the app window and the always-on-top overlay.

- `Start-Game-Overlay.bat`  
  Best for players and recordings. Opens `/play` and the tiny overlay.

- `Start-Auto.bat`  
  Full local stack: Streamlit cockpit plus product site.

- `Start-KinaestheticAI.bat`  
  Research cockpit only.

- `Start-Site.bat`  
  Product site only.

## Build portable zip

From the project root:

```powershell
.\Build-Windows-Portable.ps1
```

Output:

```text
dist\KinaestheticAI-Desktop\
dist\KinaestheticAI-Desktop.zip
```

Send the zip to testers. They unzip it and run:

```text
Start-Game-Overlay.bat
```

## Run Electron desktop beta

From the project root:

```powershell
.\Start-Desktop-App.ps1
```

First launch checks Node, installs the desktop shell dependencies, then Electron
checks/creates `.venv`, installs `requirements-local.txt`, starts
`site_server.py`, opens `/play`, and opens the topmost overlay.

## Build desktop installer

From the project root:

```powershell
.\Build-Desktop-Installer.ps1
```

Output:

```text
dist\electron\
```

The current installer milestone is for internal beta packaging. The next step is
packaging the Python engine as a sidecar executable so tester machines do not
need a repository checkout.

## Privacy

The desktop app must preserve the same product truth:

- raw video is not stored;
- raw audio is not stored;
- camera frames are not uploaded;
- only derived signals, labels, session metadata, and proof data are saved.

## Next engineering tasks

1. Add a first-run screen/checklist for Python, camera, microphone/voice, and overlay.
2. Stabilize the Electron shell in `desktop/electron`.
3. Package Python into a sidecar executable.
4. Make a branded icon and desktop shortcut installer.
5. Add a single `Kinaesthetic AI.exe` launcher wrapper.
6. Add auto-update only after the validation beta stabilizes.
