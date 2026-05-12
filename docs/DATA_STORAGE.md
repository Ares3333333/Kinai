# Kinaesthetic AI Data Storage

## Short Answer

`/play` already auto-saves each browser-CV check after consent. The browser analyzes camera locally, then sends only derived signals to the backend.

The backend writes locally to JSONL and can mirror every event to Supabase when these env vars are set:

```text
STORAGE_BACKEND=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=replace-me
SUPABASE_EVENTS_TABLE=kinaesthetic_events
```

## Flow

```text
Player clicks Start
Browser camera stays local
Browser CV creates derived signals
/api/session starts a session
/api/signals saves checks every ~1.1 sec
/api/feedback saves T/H/F labels
/camera-check saves camera readiness/failure diagnostics
/api/session ends with proof metadata
Local JSONL writes always happen
Supabase mirror happens when configured
```

## Stored

- `session_id`, `tester_id`, `game`, timestamp;
- tilt risk, readiness, recovery;
- jaw, shoulder, posture, face scores;
- signal confidence and mode labels;
- coach command text;
- player feedback labels;
- recovery proof and session summaries.
- camera readiness diagnostics: permission state, secure context, device counts, browser error name.

## Never Stored

- raw video;
- raw audio;
- camera frames;
- webcam screenshots.

## Camera Failure Cases

Send testers to `/camera-check` before `/play` when debugging launch issues.
It logs only setup metadata:

```text
result: ready / blocked / not_found / busy / timeout / https_required
permission_state
secure_context
video_input_count
error_name
browser user agent
viewport
```

This gives us a real list of why 5-10 early testers fail without storing camera frames.

## Investor Milestone

The first validation base is:

```text
100+ testers
150+ feedback labels
visible help rate
visible false alert rate
recovery proof after coach commands
```

Progress is visible on `/metrics` and `/evidence`.
