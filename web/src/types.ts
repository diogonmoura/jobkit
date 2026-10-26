export interface Env {
  ASSETS: Fetcher;
  SUPABASE_URL: string;
  SUPABASE_ANON_KEY: string;
  SUPABASE_SERVICE_ROLE_KEY?: string;
  /** "supabase" (default) or "postgres" (Neon). Unset keeps the Supabase install working as is. */
  JOBKIT_DB?: string;
  /** Postgres mode: Neon connection string. Secret. */
  DATABASE_URL?: string;
  /** Postgres mode: base URL of the Neon Auth project. */
  NEON_AUTH_BASE_URL?: string;
  /** Postgres mode: bearer token the jobkit engine uses to reach the API. Secret. */
  JOBKIT_ENGINE_TOKEN?: string;
  /** Optional: share auth cookies across subdomains (e.g. ".example.com"). Leave unset for a single host. */
  COOKIE_DOMAIN?: string;
  /** Public origin, used as the HTTP-Referer for OpenRouter. */
  SITE_ORIGIN?: string;
  /** AES-256 key (base64 or 64 hex chars) that encrypts stored LLM keys. */
  SETTINGS_ENC_KEY?: string;
  OPENROUTER_API_KEY?: string;
  ADZUNA_APP_ID?: string;
  ADZUNA_APP_KEY?: string;
  /** Adzuna country code (gb, us, de, fr, nl, …). Default gb. */
  ADZUNA_COUNTRY?: string;
  /** Optional override of the owner (otherwise the account created in the setup wizard). */
  JOBKIT_OWNER_USER_ID?: string;
  /** Optional: protects the first-run wizard on a public deploy until an owner exists. */
  SETUP_TOKEN?: string;
  SCAN_CRON_SECRET?: string;
  /** UI language: "en" (default) or "pt-PT". Also the language of AI-written scan reasons and flags. */
  JOBKIT_LANG?: string;
  /** Optional logo.dev publishable token (pk_…) for company logos; without it an initial is shown. */
  LOGO_DEV_TOKEN?: string;
  /** "builtin" (default): the /login page. "external": another app signs users in to the same Supabase project. */
  AUTH_MODE?: string;
  /** External mode: sign-in page; jobkit appends ?next=<url>. Needs COOKIE_DOMAIN shared with that app. */
  LOGIN_URL?: string;
  /** Optional: where Sign out goes (external mode hides Sign out without it). */
  LOGOUT_URL?: string;
  /** Optional: where the Settings button goes (default /setup). */
  SETTINGS_URL?: string;
  /** Optional extra nav link, e.g. back to a dashboard. */
  HOME_URL?: string;
  HOME_LABEL?: string;
  /**
   * Optional fallback for the LLM key: read an "enc:v1:" (SETTINGS_ENC_KEY) or plain key from another table,
   * as "table.column" keyed by user_id, e.g. when a companion app already stores the user's OpenRouter key.
   */
  LLM_KEY_SOURCE?: string;
}

export type PreferenceProfile = {
  roles: string[];
  seniority: string[];
  industries: string[];
  locations: string[];
  remote: boolean;
  companies: string[];
  keywords: string[];
  keywords_exclude: string[];
  salary_floor: { amount: number; currency: string; period: "year" | "month" };
  notes?: string;
  /** Title keywords per tier (T1 best). Used for prefilter ranking and as scoring context. */
  title_tiers?: Record<"T1" | "T2" | "T3" | "T4", string[]>;
  /** Companies hard-filtered before scoring (consultancies etc.), matched on company name. */
  exclude_companies?: string[];
  /** Company-name patterns that are flagged (not excluded), e.g. IT services / nearshoring. */
  flag_companies?: string[];
  /** Positive few-shot examples the user liked. */
  liked_examples?: { company: string; title: string; location: string; url: string; why?: string }[];
  /** Open fields the user fills in. */
  target_comp?: string;
  languages?: string;
  notice_period?: string;
  travel?: string;
  /** Where the candidate lives and can work (used for remote eligibility and right-to-work answers). */
  home_base?: { city: string; country: string; region: string };
  /** remote_only: fully remote roles only; remote_or_hybrid: also hybrid near home_base.city; any: no location filter. */
  remote_policy?: "remote_only" | "remote_or_hybrid" | "any";
  work_authorization?: string;
  /** What the role should be about (the "focus" criterion of the rubric), e.g. "AI adoption", "payments infrastructure". */
  focus?: string;
  /** Company types to avoid (excluded) or flag (kept with a warning). */
  company_types_avoid?: string[];
  company_types_flag?: string[];
  /** Rubric weights (points per criterion, total 100). */
  rubric?: RubricWeights;
  /** Minimum score for the daily scan to add a job. */
  min_score?: number;
  /** Free-text scoring guidance appended to the rubric (what makes a role a strong or weak fit). */
  rubric_notes?: string;
  /** Extra checks added as flags on matches; with `title` (a regex) only when the job title matches it. */
  custom_flags?: { label: string; title?: string }[];
  /** Extra writing rules for tailored CVs and application answers. */
  writing_rules?: string;
  /** Hard constraints checked by a cheap model on each posting, e.g. "Exclude roles requiring hands-on coding". */
  role_constraints?: string[];
  /** Skip engineer/developer titles before any AI call, unless clearly a product/programme/ops role. */
  exclude_engineering_titles?: boolean;
  /** Model for the suitability check (default google/gemini-2.5-flash-lite). */
  suitability_model?: string;
  /** Set when the setup wizard is finished. */
  onboarded_at?: string;
};

export type RubricWeights = { tier: number; remote: number; focus: number; seniority: number; company: number };

export type ScoreBreakdown = {
  tier: string;
  tier_points: number;
  remote: string;
  remote_points: number;
  /** How well the role matches the profile's focus (formerly ai_centrality). */
  focus: number;
  seniority_scope: number;
  company_type: string;
  company_points: number;
};

export type RawOpening = {
  externalKey: string;
  company: string;
  title: string;
  url: string;
  location: string;
  description: string;
  source: string;
  vendor?: string;
};

export type SalaryPayload = {
  published?: {
    min?: number | null;
    max?: number | null;
    currency?: string;
    period?: string;
    raw?: string;
  };
  estimate?: {
    min: number;
    median: number;
    max: number;
    currency: string;
    period: "year" | "month";
    confidence: "low" | "medium" | "high";
    sources: { title: string; url: string }[];
  };
};
