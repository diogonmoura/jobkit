import type { SupabaseClient } from "@supabase/supabase-js";
import type { Env, PreferenceProfile, RubricWeights } from "./types";
import { completeChat, type ChatMessage } from "./llm";

export const EMPTY_PROFILE: PreferenceProfile = {
  roles: [],
  seniority: [],
  industries: [],
  locations: [],
  remote: true,
  companies: [],
  keywords: [],
  keywords_exclude: [],
  salary_floor: { amount: 0, currency: "EUR", period: "year" },
  notes: "",
};

export function normalizeProfile(raw: unknown): PreferenceProfile {
  const p = (raw && typeof raw === "object" ? raw : {}) as Partial<PreferenceProfile>;
  return {
    roles: Array.isArray(p.roles) ? p.roles.map(String) : [],
    seniority: Array.isArray(p.seniority) ? p.seniority.map(String) : [],
    industries: Array.isArray(p.industries) ? p.industries.map(String) : [],
    locations: Array.isArray(p.locations) ? p.locations.map(String) : [],
    remote: Boolean(p.remote),
    companies: Array.isArray(p.companies) ? p.companies.map(String) : [],
    keywords: Array.isArray(p.keywords) ? p.keywords.map(String) : [],
    keywords_exclude: Array.isArray(p.keywords_exclude)
      ? p.keywords_exclude.map(String)
      : [],
    salary_floor: {
      amount: Number(p.salary_floor?.amount ?? 0) || 0,
      currency: String(p.salary_floor?.currency ?? "EUR"),
      period: p.salary_floor?.period === "month" ? "month" : "year",
    },
    notes: String(p.notes ?? ""),
    title_tiers: normalizeTiers(p.title_tiers),
    exclude_companies: strList(p.exclude_companies),
    flag_companies: strList(p.flag_companies),
    liked_examples: Array.isArray(p.liked_examples)
      ? p.liked_examples
          .filter((x) => x && typeof x === "object")
          .slice(0, 10)
          .map((x) => ({
            company: String(x.company ?? ""),
            title: String(x.title ?? ""),
            location: String(x.location ?? ""),
            url: String(x.url ?? ""),
            why: x.why ? String(x.why) : undefined,
          }))
      : [],
    target_comp: String(p.target_comp ?? ""),
    languages: String(p.languages ?? ""),
    notice_period: String(p.notice_period ?? ""),
    travel: String(p.travel ?? ""),
    home_base: {
      city: String(p.home_base?.city ?? ""),
      country: String(p.home_base?.country ?? ""),
      region: String(p.home_base?.region ?? ""),
    },
    remote_policy: p.remote_policy === "any" || p.remote_policy === "remote_or_hybrid" ? p.remote_policy : "remote_only",
    work_authorization: String(p.work_authorization ?? ""),
    focus: String(p.focus ?? ""),
    company_types_avoid: strList(p.company_types_avoid),
    company_types_flag: strList(p.company_types_flag),
    rubric: normalizeRubric(p.rubric),
    min_score: Math.min(100, Math.max(0, Number(p.min_score ?? 50) || 50)),
    ...(p.onboarded_at ? { onboarded_at: String(p.onboarded_at) } : {}),
    role_constraints: strList(p.role_constraints),
    exclude_engineering_titles: Boolean(p.exclude_engineering_titles),
    ...(typeof p.suitability_model === "string" && p.suitability_model.trim() ? { suitability_model: p.suitability_model.trim() } : {}),
    ...(typeof p.rubric_notes === "string" && p.rubric_notes.trim() ? { rubric_notes: p.rubric_notes } : {}),
    ...(typeof p.writing_rules === "string" && p.writing_rules.trim() ? { writing_rules: p.writing_rules } : {}),
    ...(Array.isArray(p.custom_flags)
      ? {
          custom_flags: (p.custom_flags as unknown[])
            .map((f) => (typeof f === "string" ? { label: f } : (f as { label?: unknown; title?: unknown })))
            .filter((f) => f && typeof f.label === "string" && f.label.trim())
            .map((f) => ({ label: String(f.label).trim(), ...(typeof f.title === "string" && f.title ? { title: f.title } : {}) })),
        }
      : {}),
  };
}

export const DEFAULT_RUBRIC: RubricWeights = { tier: 30, remote: 20, focus: 20, seniority: 15, company: 15 };

/** Weights are scaled so they always add up to 100. */
export function normalizeRubric(v: unknown): RubricWeights {
  const o = (v && typeof v === "object" ? v : {}) as Record<string, unknown>;
  const w: RubricWeights = { ...DEFAULT_RUBRIC };
  for (const k of Object.keys(w) as (keyof RubricWeights)[]) {
    const n = Number(o[k]);
    if (Number.isFinite(n) && n >= 0) w[k] = n;
  }
  const total = Object.values(w).reduce((a, b) => a + b, 0);
  if (!total) return { ...DEFAULT_RUBRIC };
  if (total === 100) return w;
  const out = {} as RubricWeights;
  for (const k of Object.keys(w) as (keyof RubricWeights)[]) out[k] = Math.round((w[k] * 100) / total);
  return out;
}

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.map(String).map((x) => x.trim()).filter(Boolean) : [];
}

function normalizeTiers(v: unknown): NonNullable<PreferenceProfile["title_tiers"]> {
  const o = (v && typeof v === "object" ? v : {}) as Record<string, unknown>;
  return { T1: strList(o.T1), T2: strList(o.T2), T3: strList(o.T3), T4: strList(o.T4) };
}

/** Load the free-text candidate profile (long markdown) kept next to the structured preferences. */
export async function loadCandidateProfile(supabase: SupabaseClient, userId: string): Promise<string> {
  const { data } = await supabase
    .from("jobkit_preference_profile")
    .select("candidate_profile")
    .eq("user_id", userId)
    .maybeSingle();
  return String(data?.candidate_profile ?? "");
}

export async function loadProfile(
  supabase: SupabaseClient,
  userId: string,
): Promise<PreferenceProfile> {
  const { data } = await supabase
    .from("jobkit_preference_profile")
    .select("profile")
    .eq("user_id", userId)
    .maybeSingle();
  if (!data?.profile) return { ...EMPTY_PROFILE };
  return normalizeProfile(data.profile);
}

export async function saveProfile(
  supabase: SupabaseClient,
  userId: string,
  profile: PreferenceProfile,
): Promise<void> {
  const { error } = await supabase.from("jobkit_preference_profile").upsert(
    {
      user_id: userId,
      profile,
      updated_at: new Date().toISOString(),
    },
    { onConflict: "user_id" },
  );
  if (error) throw error;
}

/**
 * Boards added on the first scan when the watchlist is empty. Deliberately empty:
 * the setup wizard (and Preferences) build the watchlist from the companies you name.
 * Add entries here only if you want a default for every new install, e.g.
 * { company: "Example Co", vendor: "greenhouse", slug: "exampleco" }.
 */
const SEED_COMPANIES: { company: string; vendor: string; slug: string }[] = [];

export async function seedScanTargets(
  supabase: SupabaseClient,
  userId: string,
  profile: PreferenceProfile,
): Promise<number> {
  const { data: existing } = await supabase
    .from("jobkit_scan_targets")
    .select("id")
    .eq("user_id", userId);
  if ((existing ?? []).length > 0) return 0;

  const preferred = new Set(profile.companies.map((c) => c.toLowerCase()));
  const matched = SEED_COMPANIES.filter((s) => preferred.has(s.company.toLowerCase()));
  const fallback = (matched.length ? matched : SEED_COMPANIES).map((s) => ({
    user_id: userId,
    id: `${s.vendor}-${s.slug}`,
    company: s.company,
    vendor: s.vendor,
    slug: s.slug,
    enabled: true,
  }));
  if (!fallback.length) return 0;
  const { error } = await supabase.from("jobkit_scan_targets").upsert(fallback, {
    onConflict: "user_id,id",
  });
  if (error) throw new Error(`scan targets: ${error.message}`);
  return fallback.length;
}

export async function seedProfileFromCv(
  env: Env,
  supabase: SupabaseClient,
  userId: string,
  request: Request | null,
): Promise<{ profile: PreferenceProfile; costUsd: number }> {
  const { data: prof } = await supabase
    .from("jobkit_profile")
    .select("master_cv")
    .eq("user_id", userId)
    .maybeSingle();
  const { data: jobs } = await supabase
    .from("jobkit_jobs")
    .select("company, role, location, scan_score")
    .eq("user_id", userId)
    .order("created_at", { ascending: false })
    .limit(30);
  const { data: apps } = await supabase
    .from("jobkit_apps")
    .select("company, role, status")
    .eq("user_id", userId)
    .limit(40);

  const messages: ChatMessage[] = [
    {
      role: "system",
      content:
        "You extract a job-search preference profile as JSON. Reply with valid JSON only, no markdown. Fields: roles, seniority, industries, locations, remote (boolean), companies, keywords, keywords_exclude, salary_floor {amount, currency, period}, notes. Write text values in English. Do not invent facts that are not in the CV or the applications.",
    },
    {
      role: "user",
      content: JSON.stringify({
        master_cv: prof?.master_cv?.slice(0, 12000) ?? "",
        saved_jobs: jobs ?? [],
        applications: apps ?? [],
      }),
    },
  ];
  const result = await completeChat(env, {
    request,
    userId,
    messages,
    json: true,
  });
  let parsed: unknown = {};
  try {
    parsed = JSON.parse(result.content);
  } catch {
    parsed = {};
  }
  const profile = normalizeProfile(parsed);
  await supabase.from("jobkit_preference_profile").upsert(
    {
      user_id: userId,
      profile,
      seeded_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
    { onConflict: "user_id" },
  );
  await seedScanTargets(supabase, userId, profile);
  return { profile, costUsd: result.costUsd };
}

export async function refreshProfileFromFeedback(
  env: Env,
  supabase: SupabaseClient,
  userId: string,
  request: Request | null,
): Promise<{ profile: PreferenceProfile; costUsd: number }> {
  const current = await loadProfile(supabase, userId);
  const since = new Date(Date.now() - 30 * 864e5).toISOString();
  const { data: feedback } = await supabase
    .from("jobkit_job_feedback")
    .select("job_id, signal, reason, created_at")
    .eq("user_id", userId)
    .gte("created_at", since)
    .order("created_at", { ascending: false })
    .limit(80);
  const { data: jobRows } = await supabase
    .from("jobkit_jobs")
    .select("id, company, role, location, scan_score, match_reason")
    .eq("user_id", userId)
    .limit(80);

  const messages: ChatMessage[] = [
    {
      role: "system",
      content:
        "You update a job-search preference profile from recent feedback. Reply with JSON only, in the same shape as the current profile. Reinforce keywords/roles/industries that match positive signals; add exclusions from 'down' and 'dismiss'. Keep salary_floor consistent. Write text values in English.",
    },
    {
      role: "user",
      content: JSON.stringify({
        current_profile: current,
        feedback: feedback ?? [],
        jobs: jobRows ?? [],
      }),
    },
  ];
  const result = await completeChat(env, {
    request,
    userId,
    messages,
    json: true,
  });
  let parsed: unknown = current;
  try {
    parsed = JSON.parse(result.content);
  } catch {
    parsed = current;
  }
  const next = normalizeProfile(parsed);
  // Never let a thin LLM answer wipe fields the user (or the seed) already filled in.
  const profile: PreferenceProfile = { ...next };
  for (const k of ["roles", "seniority", "industries", "locations", "companies", "keywords", "keywords_exclude"] as const) {
    if (!next[k].length && current[k].length) profile[k] = current[k];
  }
  if (!next.salary_floor.amount && current.salary_floor.amount) profile.salary_floor = current.salary_floor;
  if (!next.notes && current.notes) profile.notes = current.notes;
  profile.title_tiers = current.title_tiers;
  profile.exclude_companies = [...new Set([...(current.exclude_companies ?? []), ...(next.exclude_companies ?? [])])];
  profile.flag_companies = current.flag_companies;
  profile.liked_examples = current.liked_examples;
  profile.target_comp = current.target_comp;
  profile.languages = current.languages;
  profile.notice_period = current.notice_period;
  profile.travel = current.travel;
  for (const k of ["home_base", "remote_policy", "work_authorization", "focus", "company_types_avoid", "company_types_flag", "rubric", "min_score", "onboarded_at", "rubric_notes", "writing_rules", "custom_flags", "role_constraints", "exclude_engineering_titles", "suitability_model"] as const) {
    (profile as Record<string, unknown>)[k] = current[k];
  }
  if (typeof (parsed as { remote?: unknown })?.remote !== "boolean") profile.remote = current.remote;
  await saveProfile(supabase, userId, profile);
  return { profile, costUsd: result.costUsd };
}
