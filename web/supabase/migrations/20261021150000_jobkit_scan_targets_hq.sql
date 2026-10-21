-- Optional company headquarters shown as a chip on the Following tab (e.g. "Paris, France"). Empty = unknown.
alter table public.jobkit_scan_targets
  add column if not exists hq text not null default '';
