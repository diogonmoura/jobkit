-- Jobkit: preference learning, job scan list, salary cache, scan targets.
-- Apply in the shared Supabase project (owner RLS).

-- Discovered / scanned jobs (separate from application tracker rows).
create table if not exists public.jobkit_jobs (
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
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
  primary key (user_id, id),
  constraint jobkit_jobs_source_check check (
    source in ('manual', 'scan', 'import')
  ),
  constraint jobkit_jobs_scan_score_check check (
    scan_score is null or (scan_score >= 0 and scan_score <= 100)
  )
);

create unique index if not exists jobkit_jobs_external_key_idx
  on public.jobkit_jobs (user_id, external_key)
  where external_key <> '';

-- Preference profile (editable + LLM-maintained).
create table if not exists public.jobkit_preference_profile (
  user_id uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  profile jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  seeded_at timestamptz
);

-- Per-job feedback (thumbs, dismiss, implicit positives).
create table if not exists public.jobkit_job_feedback (
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  job_id text not null,
  signal text not null,
  reason text not null default '',
  created_at timestamptz not null default now(),
  primary key (user_id, job_id, signal, created_at),
  constraint jobkit_job_feedback_signal_check check (
    signal in ('up', 'down', 'dismiss', 'process', 'apply')
  ),
  foreign key (user_id, job_id) references public.jobkit_jobs (user_id, id) on delete cascade
);

-- ATS board targets for daily scan.
create table if not exists public.jobkit_scan_targets (
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  id text not null,
  company text not null,
  vendor text not null,
  slug text not null,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (user_id, id),
  constraint jobkit_scan_targets_vendor_check check (
    vendor in ('greenhouse', 'lever', 'lever-eu', 'ashby')
  )
);

-- Cached salary estimates (shared cache keyed by role/seniority/location).
create table if not exists public.jobkit_salary_cache (
  cache_key text primary key,
  role_norm text not null,
  seniority_norm text not null,
  location_norm text not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Scan run log (cost + counts).
create table if not exists public.jobkit_scan_runs (
  id bigserial primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
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

alter table public.jobkit_jobs enable row level security;
alter table public.jobkit_preference_profile enable row level security;
alter table public.jobkit_job_feedback enable row level security;
alter table public.jobkit_scan_targets enable row level security;
alter table public.jobkit_salary_cache enable row level security;
alter table public.jobkit_scan_runs enable row level security;

create policy "jobkit_jobs_owner"
  on public.jobkit_jobs for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy "jobkit_preference_profile_owner"
  on public.jobkit_preference_profile for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy "jobkit_job_feedback_owner"
  on public.jobkit_job_feedback for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy "jobkit_scan_targets_owner"
  on public.jobkit_scan_targets for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- Salary cache: readable by authenticated users; writes via service role only.
create policy "jobkit_salary_cache_read"
  on public.jobkit_salary_cache for select to authenticated
  using (true);

create policy "jobkit_scan_runs_owner"
  on public.jobkit_scan_runs for select to authenticated
  using (user_id = auth.uid());

create policy "jobkit_scan_runs_insert"
  on public.jobkit_scan_runs for insert to authenticated
  with check (user_id = auth.uid());

alter publication supabase_realtime add table public.jobkit_jobs;
alter publication supabase_realtime add table public.jobkit_preference_profile;
