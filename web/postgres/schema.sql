-- jobkit hosted tracker on plain Postgres (Neon): the end state of supabase/schema.sql plus supabase/migrations.
-- Same tables and columns, without Supabase auth: no auth.users references, no RLS, no realtime.
-- The Worker checks the owner itself. Safe to run more than once.
-- Later changes go in postgres/migrations/ as new files, never edits to this baseline.

create table if not exists jobkit_schema_migrations (
  id text primary key,
  applied_at timestamptz not null default now()
);

create table if not exists jobkit_apps (
  user_id uuid not null,
  id text not null,
  company text not null,
  role text not null,
  applied date not null,
  cv text not null default '',
  status text not null default 'applied',
  last date,
  posting text not null default '',
  events jsonb not null default '[]'::jsonb,
  cv_md text not null default '',
  posting_text text not null default '',
  updated_at timestamptz not null default now(),
  job_id text,
  answers jsonb,
  primary key (user_id, id),
  constraint jobkit_apps_status_check check (
    status in (
      'considering', 'applied', 'ack', 'screening', 'interview',
      'offer', 'rejected', 'withdrawn', 'ghosted'
    )
  )
);

create unique index if not exists jobkit_apps_user_job_uidx
  on jobkit_apps (user_id, job_id)
  where job_id is not null;

create table if not exists jobkit_profile (
  user_id uuid primary key,
  master_cv text not null default '',
  updated_at timestamptz not null default now()
);

create table if not exists jobkit_following (
  user_id uuid not null,
  id text not null default 'all',
  updated date,
  companies jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (user_id, id)
);

create table if not exists jobkit_jobs (
  user_id uuid not null,
  id text not null,
  company text not null,
  role text not null,
  posting text not null default '',
  posting_text text not null default '',
  location text not null default '',
  source text not null default 'manual',
  external_key text not null default '',
  scan_score smallint,
  match_reason text not null default '',
  is_new boolean not null default false,
  dismissed boolean not null default false,
  salary jsonb not null default '{}'::jsonb,
  benefits jsonb not null default '[]'::jsonb,
  cv_md text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  score_breakdown jsonb not null default '{}'::jsonb,
  flags jsonb not null default '[]'::jsonb,
  cv_analysis text not null default '',
  cv_generated_at timestamptz,
  cv_instructions text not null default '',
  answers jsonb,
  answers_generated_at timestamptz,
  suitability jsonb,
  primary key (user_id, id),
  constraint jobkit_jobs_source_check check (
    source in ('manual', 'scan', 'import')
  ),
  constraint jobkit_jobs_scan_score_check check (
    scan_score is null or (scan_score >= 0 and scan_score <= 100)
  )
);

create unique index if not exists jobkit_jobs_external_key_idx
  on jobkit_jobs (user_id, external_key)
  where external_key <> '';

create table if not exists jobkit_preference_profile (
  user_id uuid primary key,
  profile jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  seeded_at timestamptz,
  candidate_profile text not null default ''
);

create table if not exists jobkit_job_feedback (
  user_id uuid not null,
  job_id text not null,
  signal text not null,
  reason text not null default '',
  created_at timestamptz not null default now(),
  primary key (user_id, job_id, signal, created_at),
  constraint jobkit_job_feedback_signal_check check (
    signal in ('up', 'down', 'dismiss', 'process', 'apply')
  ),
  foreign key (user_id, job_id) references jobkit_jobs (user_id, id) on delete cascade
);

create table if not exists jobkit_scan_targets (
  user_id uuid not null,
  id text not null,
  company text not null,
  vendor text not null,
  slug text not null,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  last_scanned_at timestamptz,
  hq text not null default '',
  industry text not null default '',
  primary key (user_id, id),
  constraint jobkit_scan_targets_vendor_check check (
    vendor in ('greenhouse', 'lever', 'lever-eu', 'ashby', 'workable', 'remote-com', 'teamtailor')
  )
);

create table if not exists jobkit_salary_cache (
  cache_key text primary key,
  role_norm text not null,
  seniority_norm text not null,
  location_norm text not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists jobkit_scan_runs (
  id bigserial primary key,
  user_id uuid not null,
  ran_at timestamptz not null default now(),
  trigger text not null default 'cron',
  jobs_fetched integer not null default 0,
  jobs_scored integer not null default 0,
  jobs_inserted integer not null default 0,
  cost_usd numeric(10, 6) not null default 0,
  detail jsonb not null default '{}'::jsonb,
  constraint jobkit_scan_runs_trigger_check check (
    trigger in ('cron', 'manual')
  )
);

create table if not exists jobkit_experience_files (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  filename text not null,
  size integer not null default 0,
  content text not null default '',
  enabled boolean not null default true,
  uploaded_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  kind text not null default 'md',
  version_label text not null default '',
  version_date date
);
create index if not exists jobkit_experience_files_user on jobkit_experience_files (user_id, uploaded_at);

create table if not exists jobkit_instance (
  id smallint primary key default 1 check (id = 1),
  owner_user_id uuid not null,
  created_at timestamptz not null default now()
);

create table if not exists jobkit_settings (
  user_id uuid primary key,
  llm_provider text not null default 'openrouter',
  llm_base_url text not null default '',
  llm_model text not null default '',
  -- "enc:v1:" + base64url(iv | AES-256-GCM ciphertext), key = Worker secret SETTINGS_ENC_KEY
  llm_key_enc text not null default '',
  updated_at timestamptz not null default now(),
  constraint jobkit_settings_provider_check check (llm_provider in ('openrouter', 'openai', 'anthropic', 'custom'))
);

-- Postings the engine found on boards the Worker can't read; each scan scores and then deletes them.
create table if not exists jobkit_external_openings (
  user_id uuid not null,
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

-- The engine's merge snapshot for syncing applications.md with jobkit_apps: one per machine that syncs.
create table if not exists jobkit_sync_state (
  user_id uuid not null,
  machine text not null,
  state jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (user_id, machine)
);

-- updated_at moves on every change to an application, so the engine's sync can tell that the web app
-- edited a row between its read and its write, and pull again instead of overwriting it.
create or replace function jobkit_touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end
$$;
drop trigger if exists jobkit_apps_touch on jobkit_apps;
create trigger jobkit_apps_touch before update on jobkit_apps
  for each row execute function jobkit_touch_updated_at();
