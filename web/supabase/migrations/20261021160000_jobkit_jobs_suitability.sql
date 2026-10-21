-- Cached suitability check per job (role constraints such as "no hands-on coding"):
-- {ok, reason, model, key, checked_at}. ok=false hides the job from Jobs by default. Null = not checked.
alter table public.jobkit_jobs
  add column if not exists suitability jsonb;
