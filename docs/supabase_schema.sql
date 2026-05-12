-- Kinaesthetic AI hosted beta storage.
-- Stores only derived product events and launch diagnostics.
-- Do not add raw video/audio/frame columns.

create table if not exists public.kinaesthetic_events (
  id uuid primary key default gen_random_uuid(),
  event_type text not null,
  session_id text,
  tester_id text,
  game text,
  source text,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists kinaesthetic_events_created_at_idx
  on public.kinaesthetic_events (created_at desc);

create index if not exists kinaesthetic_events_event_type_idx
  on public.kinaesthetic_events (event_type);

create index if not exists kinaesthetic_events_session_idx
  on public.kinaesthetic_events (session_id);

create index if not exists kinaesthetic_events_tester_idx
  on public.kinaesthetic_events (tester_id);

create index if not exists kinaesthetic_events_payload_gin_idx
  on public.kinaesthetic_events using gin (payload);

create or replace view public.kinaesthetic_camera_failures as
select
  created_at,
  event_type,
  session_id,
  tester_id,
  game,
  payload->>'result' as result,
  payload->'diagnostics'->>'permission_state' as permission_state,
  payload->'diagnostics'->>'error_name' as error_name,
  payload->'diagnostics'->>'is_secure_context' as is_secure_context,
  payload->'diagnostics'->>'video_input_count' as video_input_count
from public.kinaesthetic_events
where event_type = 'camera_check'
  and coalesce(payload->>'result', '') <> 'ready';

alter table public.kinaesthetic_events enable row level security;

-- The backend uses SUPABASE_SERVICE_ROLE_KEY and bypasses RLS.
-- Keep anon/client inserts disabled so browser clients cannot write directly.
