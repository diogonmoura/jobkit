-- Jobkit: free-text candidate profile, score breakdown + flags on scanned jobs, scan target rotation.
alter table public.jobkit_preference_profile
  add column if not exists candidate_profile text not null default '';

alter table public.jobkit_jobs
  add column if not exists score_breakdown jsonb not null default '{}'::jsonb,
  add column if not exists flags jsonb not null default '[]'::jsonb;

alter table public.jobkit_scan_targets
  add column if not exists last_scanned_at timestamptz;
