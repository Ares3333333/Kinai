# Game Recording Mode

Use this when recording a match with Kinaesthetic AI on top.

## Start

Double-click:

```text
Start-Game-Overlay.bat
```

It opens:

- `/play` for camera and local Browser CV;
- `/overlay?mode=tiny&voice=1&window=1` as a small always-on-top overlay.

## Recording Steps

1. In `/play`, click **Старт с камерой** and allow camera access.
2. In the small overlay, click **Включить голос** once.
3. Start the game in borderless/windowed mode.

Exclusive fullscreen can hide normal Windows overlay windows. If that happens, switch the game to borderless fullscreen or add `/overlay` as an OBS browser source.

## Online Site Mode

For a deployed HTTPS site, set `KAI_SITE_URL` before launching:

```powershell
$env:KAI_SITE_URL="https://your-domain.example"
.\Start-Game-Overlay.ps1
```

The launcher will use:

- `https://your-domain.example/play`
- `https://your-domain.example/overlay?mode=tiny&voice=1&window=1`

The local Python server is only started when the URL is `localhost`.

## OBS Reliable Overlay

For the most reliable recording over any fullscreen game, use OBS:

1. Add **Browser Source**.
2. URL: `http://localhost:8502/overlay?mode=tiny&voice=1`
3. Width: `360`
4. Height: `260`
5. Put the source above the game capture.

For an online deployment, replace the URL with:

```text
https://your-domain.example/overlay?mode=tiny&voice=1
```

## Hotkeys

- `T`: felt tilt
- `H`: command helped
- `F`: false alert
- `M`: mute/unmute overlay voice

Raw video, audio, and frames are not stored. The overlay reads only derived state from the local site server.
