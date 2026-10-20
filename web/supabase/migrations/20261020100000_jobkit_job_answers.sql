-- Jobkit: drafted answers to a job's application-form questions (generated once, edited by hand).
alter table public.jobkit_jobs
  add column if not exists answers jsonb,
  add column if not exists answers_generated_at timestamptz;
