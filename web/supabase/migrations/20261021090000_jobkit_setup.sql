-- First-run setup: single owner of this jobkit instance + per-user LLM settings.
-- Both tables are service-role only (RLS on, no policies): the browser never reads them.

create table if not exists public.jobkit_instance (
  id smallint primary key default 1 check (id = 1),
  owner_user_id uuid not null references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table public.jobkit_instance enable row level security;

create table if not exists public.jobkit_settings (
  user_id uuid primary key references auth.users (id) on delete cascade,
  llm_provider text not null default 'openrouter',
  llm_base_url text not null default '',
  llm_model text not null default '',
  -- "enc:v1:" + base64url(iv | AES-256-GCM ciphertext), key = Worker secret SETTINGS_ENC_KEY
  llm_key_enc text not null default '',
  updated_at timestamptz not null default now(),
  constraint jobkit_settings_provider_check check (llm_provider in ('openrouter', 'openai', 'custom'))
);
alter table public.jobkit_settings enable row level security;
