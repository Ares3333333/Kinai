# Kinaesthetic AI MVP

Kinaesthetic AI — real-time anti-tilt и performance coaching для геймеров.

## Что запускаем

- `app.py` — Streamlit cockpit (локальный research-движок): `http://localhost:8501`
- `site_server.py` + `pitch_site/` — продуктовый сайт/демо/API: `http://localhost:8502`

## Быстрый старт

```powershell
streamlit run app.py
```

```powershell
python site_server.py
```

## Основные URL

- Landing: `http://localhost:8502/`
- Demo: `http://localhost:8502/demo`
- Player mode: `http://localhost:8502/play`
- Evidence: `http://localhost:8502/evidence`
- Overlay: `http://localhost:8502/overlay`
- API state: `http://localhost:8502/api/state`

## Приватность

- Raw video/audio не сохраняются по умолчанию.
- Сохраняются только производные сигналы, session events и labels.
- Consent логируется в `data/consent_log.jsonl`.

## Tester links

Пример:

```text
http://localhost:8502/play?tester=alex&game=valorant
```

Hotkeys:

- `T` — почувствовал тильт
- `H` — подсказка помогла
- `F` — false alert

## Cloud coach

Поддерживается каскад:

1. Groq
2. Gemini
3. Local fallback

Настройка через `.env`.

## Важно

Kinaesthetic AI — инструмент performance coaching. Это не медицинская диагностика и не лечение.
