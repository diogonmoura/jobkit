-- jobkit hosted tracker: base tables
-- Run in your Supabase project (SQL editor), or use the combined setup file from the setup wizard.
--
-- If upgrading from the earlier single-column id PK, drop the old tables first
-- (export data if needed): drop table public.jobkit_apps; drop table public.jobkit_following;

create table if not exists public.jobkit_apps (
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
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
  primary key (user_id, id),
  constraint jobkit_apps_status_check check (
    status in (
      'considering', 'applied', 'ack', 'screening', 'interview',
      'offer', 'rejected', 'withdrawn', 'ghosted'
    )
  )
);

create table if not exists public.jobkit_profile (
  user_id uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  master_cv text not null default '',
  updated_at timestamptz not null default now()
);

create table if not exists public.jobkit_following (
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  id text not null default 'all',
  updated date,
  companies jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (user_id, id)
);

alter table public.jobkit_apps enable row level security;
alter table public.jobkit_profile enable row level security;
alter table public.jobkit_following enable row level security;

drop policy if exists "jobkit_apps_authenticated_all" on public.jobkit_apps;
drop policy if exists "jobkit_following_authenticated_all" on public.jobkit_following;

create policy "jobkit_apps_owner"
  on public.jobkit_apps for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy "jobkit_following_owner"
  on public.jobkit_following for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy "jobkit_profile_owner"
  on public.jobkit_profile for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- Realtime (enable in Dashboard → Database → Publications if ALTER fails)
alter publication supabase_realtime add table public.jobkit_apps;
alter publication supabase_realtime add table public.jobkit_following;
alter publication supabase_realtime add table public.jobkit_profile;


