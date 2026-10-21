-- Optional short industry label per watched company (e.g. "Fintech"), shown as a chip on Following. Empty = unknown.
alter table public.jobkit_scan_targets
  add column if not exists industry text not null default '';
