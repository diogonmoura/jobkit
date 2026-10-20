-- Allow Remote.com's own ATS (apply.remote.com) as a scan target.
alter table public.jobkit_scan_targets drop constraint if exists jobkit_scan_targets_vendor_check;
alter table public.jobkit_scan_targets add constraint jobkit_scan_targets_vendor_check
  check (vendor in ('greenhouse', 'lever', 'lever-eu', 'ashby', 'workable', 'remote-com'));
