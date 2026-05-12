# Kinaesthetic AI deploy-ready checklist

## Public demo path

1. Open `/` and use the investor path: `/demo` -> `/evidence` -> `/play`.
2. `/demo` must clearly say `DEMO` and show the 28-second signal -> alert -> recovery proof story.
3. `/play` must request camera permission only after the user clicks `Старт с камерой`.
4. `/evidence` must show proof/privacy without implying that demo data is live.

## Hosting plan

Recommended for the next public test:

- Static site on Vercel.
- Python API (`site_server.py`) on Render, Railway, or Fly when live logging/proof persistence is needed.
- Vercel serves `/`, `/demo`, `/evidence`, `/privacy`, `/play` as static product pages.
- Vercel function `api/[...path].js` proxies `/api/*` to `KAI_BACKEND_URL` when configured.
- If `KAI_BACKEND_URL` is missing, `/api/*` returns honest public-demo fallback JSON instead of breaking the site.
- No raw video/audio upload. Browser CV stays local.

## Vercel env

```text
KAI_BACKEND_URL=https://your-backend-domain.onrender.com
```

Leave it empty for public demo-only mode.

## Pre-demo commands

Run from `C:\Users\user\Desktop\MeirX`:

```powershell
.\.venv\Scripts\python.exe tools\pre_demo_check.py
node --check pitch_site\product_session.js
node --check pitch_site\browser_cv.js
node --check pitch_site\investor_demo.js
node --check pitch_site\evidence.js
npm run test:e2e
```

## Final manual checks

- Camera starts on `/play` in Chrome or Edge over `https://` or `localhost`.
- Badges never show stale/offline data as live.
- Investor demo ends with `Tilt 74 -> 31`.
- Evidence page exports a package without raw media.
- Contact CTA opens an investor/pilot email intro.
- Mobile first viewport has no overlap or clipped buttons.
