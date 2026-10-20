-- Jobkit: experience files can be PDFs/DOCX (text extracted in the browser) and carry a version date/label.
alter table public.jobkit_experience_files
  add column if not exists kind text not null default 'md',
  add column if not exists version_label text not null default '',
  add column if not exists version_date date;
