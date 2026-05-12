# Kinaesthetic AI Agent Rules

## Mission
- Work on the product site first: `pitch_site/*`, `site_server.py`, deploy docs, and tests.
- Treat `app.py` as the Streamlit cockpit. Do not rewrite it or remove `render_*` functions.
- The product is performance coaching for gamers. Never add medical diagnosis claims.

## Product Truth
- Raw video, audio, and frames must never be stored or uploaded.
- Browser camera analysis must stay local. Backend may receive only derived signals, labels, proof/session metadata, and health data.
- LIVE means a real fresh signal is updating. STALE, DEMO, and OFFLINE must be visibly labeled and must not look like real live metrics.
- Critical alerts must work through local templates even when Groq/Gemini/LLM is down.

## Frontend Standard
- `/play` is the main product screen: camera, LIVE/DEMO/OFFLINE, tilt risk, readiness, coach command, recovery proof.
- Keep player mode simple. Put tester, validation, export, calibration, and technical panels behind advanced/investor controls.
- Do not ship mojibake. Run a text scan before handoff.
- No overlapping cards, clipped video, hidden start button, or unreadable mobile layout.

## Safe Files
- Use `atomic_io.py` helpers for JSON/JSONL writes.
- Do not directly write derived data files with ad hoc `write_text`.
- Do not add auth, payments, cloud upload, Electron, Overwolf, or full backend migration unless the user explicitly asks.

## Required Local Checks
Run from `C:\Users\user\Desktop\MeirX` before saying ready:

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py scoring.py site_server.py atomic_io.py alerts.py product_intelligence.py
.\.venv\Scripts\python.exe -m pytest tests/ -q
node --check pitch_site/product_session.js
node --check pitch_site/browser_cv.js
node --check pitch_site/app.js
npm run test:e2e
.\.venv\Scripts\python.exe tools\pre_demo_check.py
```

## Visual QA
- Verify `/`, `/play`, `/demo`, `/overlay`, `/evidence`, `/metrics`, and `/privacy` on desktop and mobile.
- On `/play`, test a camera start with fake media or a real browser permission flow.
- Confirm `video.srcObject` is present, the placeholder is hidden, mode becomes LIVE, and no card overlaps the video.

## Deploy Reality
- Vercel can host the static frontend.
- Long-running Python backend plus local JSONL writes should run on Render, Railway, Fly, or another persistent service.
- If using Vercel for `/api/*`, configure rewrites/proxy to the backend domain and keep frontend fallbacks honest.
