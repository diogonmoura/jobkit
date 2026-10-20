-- Jobkit: detailed experience notes (.md/.txt) uploaded by the user; richest factual source for CVs and answers.
create table if not exists public.jobkit_experience_files (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  filename text not null,
  size integer not null default 0,
  content text not null default '',
  enabled boolean not null default true,
  uploaded_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists jobkit_experience_files_user on public.jobkit_experience_files (user_id, uploaded_at);
alter table public.jobkit_experience_files enable row level security;
drop policy if exists "jobkit_experience_files_own" on public.jobkit_experience_files;
create policy "jobkit_experience_files_own" on public.jobkit_experience_files
  for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
