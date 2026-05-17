# Kinaesthetic AI Desktop App Plan

## Decision

Yes: Kinaesthetic AI should become a downloadable Windows app.

The best path is staged:

1. **Now: portable Windows app**
   - ship a zip/folder;
   - user runs `Start-Game-Overlay.bat`;
   - first launch creates `.venv`, installs `requirements-local.txt`, starts the local server, opens `/play`, and opens the tiny overlay;
   - raw video/audio/frames stay local.

2. **Next: signed installer**
   - same runtime, but packaged with an installer;
   - Start Menu/Desktop shortcuts;
   - clearer first-run camera and voice setup;
   - optional auto-update later.

3. **Later: native shell**
   - Tauri or Electron wrapper if we need a single polished window/tray app;
   - keep the Python/CV engine local;
   - avoid breaking browser camera reliability too early.

## Why not rewrite now

The current local stack already works:

- `app.py` is the Streamlit research cockpit.
- `site_server.py` serves `/play`, `/overlay`, `/demo`, `/evidence`, and local APIs.
- Browser CV is already reliable in Edge/Chrome.
- The overlay already works as a small topmost browser-app window.

Rewriting into native UI before packaging would risk camera permission bugs and delay validation.

## Recommended user-facing launchers

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

## Privacy

The desktop app must preserve the same product truth:

- raw video is not stored;
- raw audio is not stored;
- camera frames are not uploaded;
- only derived signals, labels, session metadata, and proof data are saved.

## Next engineering tasks

1. Add a first-run screen/checklist for Python, camera, microphone/voice, and overlay.
2. Make a branded icon and desktop shortcut installer.
3. Add a single `Kinaesthetic AI.exe` launcher wrapper.
4. Add auto-update only after the validation beta stabilizes.
