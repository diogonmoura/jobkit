-- Claude (the Anthropic API) joins the LLM providers chosen in /setup.
alter table public.jobkit_settings drop constraint if exists jobkit_settings_provider_check;
alter table public.jobkit_settings add constraint jobkit_settings_provider_check
  check (llm_provider in ('openrouter', 'openai', 'anthropic', 'custom'));
