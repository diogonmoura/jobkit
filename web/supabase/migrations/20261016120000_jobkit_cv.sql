-- Master CV profile + tailored CV markdown per application.
-- Apply manually in the shared Supabase project when ready (not applied by the agent).

create table if not exists public.jobkit_profile (
  user_id uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  master_cv text not null default '',
  updated_at timestamptz not null default now()
);

alter table public.jobkit_profile enable row level security;

drop policy if exists "jobkit_profile_owner" on public.jobkit_profile;
create policy "jobkit_profile_owner"
  on public.jobkit_profile for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

alter table public.jobkit_apps
  add column if not exists cv_md text not null default '',
  add column if not exists posting_text text not null default '';

-- Realtime (enable in Dashboard → Database → Publications if ALTER fails)
alter publication supabase_realtime add table public.jobkit_profile;
