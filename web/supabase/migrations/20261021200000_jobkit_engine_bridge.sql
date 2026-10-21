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

-- updated_at moves on every change to an application, so the engine's sync can tell that the web app
-- edited a row between its read and its write, and pull again instead of overwriting it.
create or replace function public.jobkit_touch_updated_at() returns trigger
language plpgsql set search_path = public as $$
begin
  new.updated_at = now();
  return new;
end
$$;
drop trigger if exists jobkit_apps_touch on public.jobkit_apps;
create trigger jobkit_apps_touch before update on public.jobkit_apps
  for each row execute function public.jobkit_touch_updated_at();
