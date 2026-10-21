-- Teamtailor career sites (slug = the career-site host; read from its public jobs.rss feed).
alter table public.jobkit_scan_targets drop constraint if exists jobkit_scan_targets_vendor_check;
alter table public.jobkit_scan_targets add constraint jobkit_scan_targets_vendor_check
  check (vendor in ('greenhouse', 'lever', 'lever-eu', 'ashby', 'workable', 'remote-com', 'teamtailor'));
