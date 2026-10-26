import type { BrowserAccess } from "./types";

const ALL = ["select", "insert", "update", "upsert", "delete"] as const;

// What the browser may do through /api/db, per table. Mirrors the Supabase RLS policies; a table
// that is not listed (settings, instance, external openings, sync state, migrations) stays closed.
export const BROWSER_ACCESS: BrowserAccess = {
  jobkit_apps: [...ALL],
  jobkit_following: [...ALL],
  jobkit_profile: [...ALL],
  jobkit_jobs: [...ALL],
  jobkit_preference_profile: [...ALL],
  jobkit_job_feedback: [...ALL],
  jobkit_scan_targets: [...ALL],
  jobkit_experience_files: [...ALL],
  jobkit_salary_cache: ["select"],
  jobkit_scan_runs: ["select", "insert"],
};
