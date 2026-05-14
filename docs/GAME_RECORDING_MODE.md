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

## Hotkeys

- `T`: felt tilt
- `H`: command helped
- `F`: false alert
- `M`: mute/unmute overlay voice

Raw video, audio, and frames are not stored. The overlay reads only derived state from the local site server.
