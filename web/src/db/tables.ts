import type { TableSpec } from "./types";

// Every table and column the Postgres compiler may put in SQL. schema.test.ts checks this against
// supabase/ and postgres/. owned = has user_id; primaryKey is the default upsert conflict target.
export const TABLES: Record<string, TableSpec> = {
  jobkit_apps: {
    columns: ["user_id", "id", "company", "role", "applied", "cv", "status", "last", "posting", "events", "cv_md", "posting_text", "updated_at", "job_id", "answers"],
    jsonColumns: ["events", "answers"],
    owned: true,
    primaryKey: ["user_id", "id"],
  },
  jobkit_profile: {
    columns: ["user_id", "master_cv", "updated_at"],
    jsonColumns: [],
    owned: true,
    primaryKey: ["user_id"],
  },
  jobkit_following: {
    columns: ["user_id", "id", "updated", "companies", "updated_at"],
    jsonColumns: ["companies"],
    owned: true,
    primaryKey: ["user_id", "id"],
  },
  jobkit_jobs: {
    columns: [
      "user_id", "id", "company", "role", "posting", "posting_text", "location", "source", "external_key", "scan_score", "match_reason",
      "is_new", "dismissed", "salary", "benefits", "cv_md", "created_at", "updated_at", "score_breakdown", "flags", "cv_analysis",
      "cv_generated_at", "cv_instructions", "answers", "answers_generated_at", "suitability",
    ],
    jsonColumns: ["salary", "benefits", "score_breakdown", "flags", "answers", "suitability"],
    owned: true,
    primaryKey: ["user_id", "id"],
  },
  jobkit_preference_profile: {
    columns: ["user_id", "profile", "updated_at", "seeded_at", "candidate_profile"],
    jsonColumns: ["profile"],
    owned: true,
    primaryKey: ["user_id"],
  },
  jobkit_job_feedback: {
    columns: ["user_id", "job_id", "signal", "reason", "created_at"],
    jsonColumns: [],
    owned: true,
    primaryKey: ["user_id", "job_id", "signal", "created_at"],
  },
  jobkit_scan_targets: {
    columns: ["user_id", "id", "company", "vendor", "slug", "enabled", "created_at", "last_scanned_at", "hq", "industry"],
    jsonColumns: [],
    owned: true,
    primaryKey: ["user_id", "id"],
  },
  jobkit_salary_cache: {
    columns: ["cache_key", "role_norm", "seniority_norm", "location_norm", "payload", "created_at", "updated_at"],
    jsonColumns: ["payload"],
    owned: false,
    primaryKey: ["cache_key"],
  },
  jobkit_scan_runs: {
    columns: ["id", "user_id", "ran_at", "trigger", "jobs_fetched", "jobs_scored", "jobs_inserted", "cost_usd", "detail"],
    jsonColumns: ["detail"],
    owned: true,
    primaryKey: ["id"],
  },
  jobkit_experience_files: {
    columns: ["id", "user_id", "filename", "size", "content", "enabled", "uploaded_at", "updated_at", "kind", "version_label", "version_date"],
    jsonColumns: [],
    owned: true,
    primaryKey: ["id"],
  },
  jobkit_instance: {
    columns: ["id", "owner_user_id", "created_at"],
    jsonColumns: [],
    owned: false,
    primaryKey: ["id"],
  },
  jobkit_settings: {
    columns: ["user_id", "llm_provider", "llm_base_url", "llm_model", "llm_key_enc", "updated_at"],
    jsonColumns: [],
    owned: true,
    primaryKey: ["user_id"],
  },
  jobkit_external_openings: {
    columns: ["user_id", "external_key", "company", "title", "url", "description", "location", "source", "found_at"],
    jsonColumns: [],
    owned: true,
    primaryKey: ["user_id", "external_key"],
  },
  jobkit_sync_state: {
    columns: ["user_id", "machine", "state", "updated_at"],
    jsonColumns: ["state"],
    owned: true,
    primaryKey: ["user_id", "machine"],
  },
};
