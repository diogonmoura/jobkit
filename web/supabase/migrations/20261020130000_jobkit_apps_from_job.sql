-- Link tracker applications to scanned jobs ("Candidatei-me" on a job card).
alter table public.jobkit_apps
  add column if not exists job_id text,
  add column if not exists answers jsonb;

create unique index if not exists jobkit_apps_user_job_uidx
  on public.jobkit_apps (user_id, job_id)
  where job_id is not null;
