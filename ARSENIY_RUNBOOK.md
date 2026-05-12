# Kinaesthetic AI · Runbook for Arseniy

Короткий операционный гайд: как запустить систему, как проверить качество перед демо, как собрать proof-пакет и куда смотреть при сбоях.

## 1) Что здесь есть

- `app.py` — локальный Streamlit engine (`http://localhost:8501`)
- `site_server.py` — сайт + API + demo surfaces (`http://localhost:8502`)
- `pitch_site/play` — главный продуктовый экран для игрока/инвестора
- `pitch_site/evidence` — витрина доказательств и экспортов

## 2) Быстрый запуск (локально)

В двух терминалах:

```powershell
streamlit run app.py
```

```powershell
python site_server.py
```

Проверка:

- `http://localhost:8501` — cockpit открыт
- `http://localhost:8502` — landing открыт
- `http://localhost:8502/play` — player mode открыт

## 3) One-command quality check (перед демо)

Одна команда:

```powershell
npm run verify:local
```

Что входит:

- Python compile checks
- `pytest` (backend/unit tests)
- JS syntax checks
- Playwright smoke (`/play` + demo lock flow)

Быстрый режим без e2e:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Run-Local-CI.ps1 -SkipE2E
```

## 4) Основные рабочие URL

- Landing: `http://localhost:8502/`
- Demo: `http://localhost:8502/demo`
- Player mode: `http://localhost:8502/play`
- Evidence: `http://localhost:8502/evidence`
- Overlay: `http://localhost:8502/overlay`
- State API: `http://localhost:8502/api/state`
- System health API: `http://localhost:8502/api/system-health`

## 5) Runtime-режимы (единый словарь)

Во всех ключевых поверхностях:

- `LIVE` — идет реальный анализ
- `DEMO` — демонстрационный/fallback сценарий
- `STALE` — сигнал устарел
- `OFFLINE` — анализ не идет

Если `numbers_visible=false`, числовые метрики скрываются (`—`) — это ожидаемое поведение.

## 6) Как проводить tester-сессию

1. Открыть `http://localhost:8502/play?autostart=1&tester=<id>&game=<game>`
2. Дать доступ к камере
3. Играть 5–10 минут
4. Ставить labels:
   - `T` — felt tilt
   - `H` — helped
   - `F` — false alert
5. После стопа проверить `Recovery proof` и `Session Score`

## 7) Экспорт артефактов для инвестора

На `http://localhost:8502/evidence`:

- `Экспорт pitch package` — полный founder deck пакет (JSON артефакты)
- `Экспорт proof bundle` — быстрый proof (JSON + PNG, если proof не synthetic)

API-экспорт:

- `GET /api/export-founder-deck`
- `GET /api/share-proof`
- `GET /api/proof-share`

## 8) Что делать, если что-то сломалось

### Камера не стартует

- убедиться, что это Chrome/Edge
- проверить разрешение камеры в браузере
- закрыть приложения, занявшие камеру (Zoom/Discord/OBS)
- для удаленного доступа использовать HTTPS (не только HTTP)

### На `/play` высокий шум/дергание

- в UI выбрать `Sensitivity: normal` (или `low`)
- проверить свет и видимость лица/плеч
- Browser CV hardening уже включен (freeze guard + anti-spike + confidence EMA)

### Cloud coach нестабилен

- смотреть `LLM health` блок на `/play`
- `fallback` — нормальный safe режим
- проверить `/api/llm-health`
- проверить ключи Groq/Gemini в `.env`

### Runtime не LIVE

- проверить `http://localhost:8501` (engine)
- проверить `/api/system-health`
- если `STALE/OFFLINE`, перезапустить сессию на `/play`

## 9) Файлы данных (локально)

Основные:

- `data/public_browser_signals.jsonl`
- `data/public_sessions.jsonl`
- `data/tester_feedback.jsonl`
- `data/consent_log.jsonl`
- `data/founder_exports/*`

Принцип privacy:

- raw video/audio **не сохраняются**
- сохраняются только производные сигналы и labels

## 10) Мини-checklist перед live demo

1. `npm run verify:local` — green
2. `/play` открывается, камера запускается, status = `LIVE`
3. На стресс-экспрессии растут `Tilt`/cue chips
4. Работает `Demo lock 90s`
5. На `/evidence` экспортируются package и proof bundle

Если эти 5 пунктов ок — сборка готова к показу тестерам и инвестору.
