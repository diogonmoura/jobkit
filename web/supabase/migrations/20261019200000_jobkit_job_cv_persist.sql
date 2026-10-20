-- Jobkit: persist the generated CV analysis and generation metadata per job.
alter table public.jobkit_jobs
  add column if not exists cv_analysis text not null default '',
  add column if not exists cv_generated_at timestamptz,
  add column if not exists cv_instructions text not null default '';
