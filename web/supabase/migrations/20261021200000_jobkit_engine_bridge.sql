-- Bridge to the jobkit engine (the Python CLI). Only the service role uses these tables (no policies).

-- Postings the engine found on boards the Worker can't read; each scan scores and then deletes them.
create table if not exists public.jobkit_external_openings (
  user_id uuid not null references auth.users (id) on delete cascade,
  external_key text not null,
  company text not null,
  title text not null,
  url text not null,
  description text not null default '',
  location text not null default '',
  source text not null default '',
  found_at timestamptz not null default now(),
  primary key (user_id, external_key)
);
alter table public.jobkit_external_openings enable row level security;

-- The engine's merge snapshot for syncing applications.md with jobkit_apps, shared by every machine that syncs.
create table if not exists public.jobkit_sync_state (
  user_id uuid primary key references auth.users (id) on delete cascade,
  state jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
alter table public.jobkit_sync_state enable row level security;
