import type { SupabaseClient } from "@supabase/supabase-js";
import { createServiceSupabase, createSupabaseFromRequest, getOwnerId, jsonResponse, supabaseConfigured } from "./auth";
import { encryptSecret, loadLlmSettings, PROVIDERS, testLlmKey, type LlmProvider } from "./llm";
import { loadProfile, normalizeProfile, saveProfile } from "./preferences";
import type { Env, PreferenceProfile } from "./types";

/**
 * First-run setup wizard API (/api/setup/*).
 * - Supabase credentials live in Worker vars/secrets; the wizard only tests them and prints what to set.
 * - The service role key is used here, server-side, and is never returned to the browser.
 */

const TABLES = [
  "jobkit_apps",
  "jobkit_following",
  "jobkit_profile",
  "jobkit_jobs",
  "jobkit_preference_profile",
  "jobkit_job_feedback",
  "jobkit_scan_targets",
  "jobkit_salary_cache",
  "jobkit_scan_runs",
  "jobkit_experience_files",
  "jobkit_instance",
  "jobkit_settings",
];

type Check = { ok: boolean; detail: string };

function cleanUrl(u: string): string {
  return String(u || "").trim().replace(/\/+$/, "");
}

/** Test a Supabase URL + publishable (anon) key + service role key without storing anything. */
export async function testSupabase(url: string, anonKey: string, serviceKey: string): Promise<{
  url: Check;
  anonKey: Check;
  serviceKey: Check;
  schema: Check & { missing: string[] };
}> {
  const base = cleanUrl(url);
  const out = {
    url: { ok: false, detail: "" },
    anonKey: { ok: false, detail: "" },
    serviceKey: { ok: false, detail: "" },
    schema: { ok: false, detail: "", missing: [] as string[] },
  };
  if (!/^https?:\/\/[^/]+$/.test(base)) {
    out.url.detail = "Use the project URL, e.g. https://abcdefgh.supabase.co";
    return out;
  }
  try {
    const r = await fetch(`${base}/auth/v1/settings`, { headers: { apikey: anonKey } });
    out.url = { ok: r.status !== 404, detail: r.status === 404 ? "No Supabase project answers at this URL" : "Project reachable" };
    out.anonKey = r.ok ? { ok: true, detail: "Publishable (anon) key accepted" } : { ok: false, detail: `Publishable key rejected (HTTP ${r.status})` };
  } catch (e) {
    out.url.detail = `Could not reach the URL: ${e instanceof Error ? e.message : e}`;
    return out;
  }
  if (/^sb_secret_/.test(anonKey) || jwtRole(anonKey) === "service_role") {
    out.anonKey = { ok: false, detail: "This is a secret key. Use the publishable (anon) key here; the secret goes in the service role field." };
  }
  try {
    const r = await fetch(`${base}/auth/v1/admin/users?per_page=1`, { headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}` } });
    out.serviceKey = r.ok
      ? { ok: true, detail: "Service role key accepted (kept server-side only)" }
      : { ok: false, detail: `Service role key rejected (HTTP ${r.status}). Use the secret / service_role key, not the publishable one.` };
  } catch (e) {
    out.serviceKey.detail = String(e instanceof Error ? e.message : e);
  }
  if (out.serviceKey.ok) {
    const missing: string[] = [];
    for (const t of TABLES) {
      const r = await fetch(`${base}/rest/v1/${t}?select=*&limit=0`, { headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}` } });
      if (!r.ok) missing.push(t);
    }
    out.schema = missing.length
      ? { ok: false, detail: `${missing.length} table(s) missing: run the setup SQL`, missing }
      : { ok: true, detail: "All jobkit tables are present", missing };
  } else {
    out.schema.detail = "Needs a valid service role key to check";
  }
  return out;
}

function jwtRole(key: string): string {
  try {
    const p = key.split(".")[1];
    return JSON.parse(atob(p.replace(/-/g, "+").replace(/_/g, "/"))).role ?? "";
  } catch {
    return "";
  }
}

function encKeyOk(env: Env): boolean {
  const raw = (env.SETTINGS_ENC_KEY ?? "").trim();
  if (/^[0-9a-f]{64}$/i.test(raw)) return true;
  try {
    return atob(raw.replace(/-/g, "+").replace(/_/g, "/")).length >= 32;
  } catch {
    return false;
  }
}

async function currentUser(request: Request, env: Env) {
  if (!supabaseConfigured(env)) return { user: null, supabase: null, pending: [] as never[] };
  const { supabase, pending } = createSupabaseFromRequest(request, env);
  const { data } = await supabase.auth.getUser();
  return { user: data.user, supabase, pending };
}

/** Slugs to try for a company name on public ATS boards. */
function slugCandidates(name: string): string[] {
  const base = name.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "").replace(/&/g, "and").trim();
  const joined = base.replace(/[^a-z0-9]+/g, "");
  const dashed = base.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  // Only whole-name slugs: a first-word guess ("no" for "No Such Co") matches unrelated boards.
  return [...new Set([joined, dashed].filter((s) => s.length > 1))];
}

export type BoardHit = { company: string; vendor: string; slug: string; jobs: number };

/** Public RSS feed of a Teamtailor career site (slug = its host, e.g. careers.example.com). */
export const teamtailorFeed = (host: string) => `https://${host.replace(/[^a-z0-9.-]/gi, "")}/jobs.rss`;

/** Turn a board URL into vendor + slug. */
export function parseBoardUrl(raw: string): { vendor: string; slug: string } | null {
  let u: URL;
  try {
    u = new URL(raw.trim());
  } catch {
    return null;
  }
  const host = u.hostname.toLowerCase();
  const first = u.pathname.split("/").filter(Boolean)[0] ?? "";
  if (/(^|\.)ashbyhq\.com$/.test(host) && first) return { vendor: "ashby", slug: first };
  if (/greenhouse\.io$/.test(host)) {
    const slug = u.searchParams.get("for") || first;
    return slug ? { vendor: "greenhouse", slug } : null;
  }
  if (host === "jobs.lever.co" && first) return { vendor: "lever", slug: first };
  if (host === "jobs.eu.lever.co" && first) return { vendor: "lever-eu", slug: first };
  if (host === "apply.workable.com" && first) return { vendor: "workable", slug: first };
  if (host === "apply.remote.com") return { vendor: "remote-com", slug: "remote" };
  // Teamtailor career sites: <company>.teamtailor.com or a custom domain (detected from the page); slug = career-site host.
  if (/\.teamtailor\.com$/.test(host)) return { vendor: "teamtailor", slug: host };
  return null;
}

/** Ask a board how many open jobs it has; null when the board does not exist. */
export async function probe(vendor: string, slug: string): Promise<number | null> {
  const s = encodeURIComponent(slug);
  const urls: Record<string, string> = {
    ashby: `https://api.ashbyhq.com/posting-api/job-board/${s}`,
    greenhouse: `https://boards-api.greenhouse.io/v1/boards/${s}/jobs`,
    lever: `https://api.lever.co/v0/postings/${s}?mode=json`,
    "lever-eu": `https://api.eu.lever.co/v0/postings/${s}?mode=json`,
    workable: `https://apply.workable.com/api/v1/widget/accounts/${s}`,
    "remote-com": "https://apply.remote.com/api/public/jobs",
  };
  if (vendor === "teamtailor") {
    try {
      const r = await fetch(teamtailorFeed(slug), { headers: { "User-Agent": "Mozilla/5.0 (compatible; jobkit-setup/1)" } });
      if (!r.ok) return null;
      const xml = await r.text();
      return /<rss[\s>]/.test(xml) ? (xml.match(/<item>/g) ?? []).length : null;
    } catch {
      return null;
    }
  }
  const url = urls[vendor];
  if (!url) return null;
  try {
    const r = await fetch(url, { headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 (compatible; jobkit-setup/1)" } });
    if (!r.ok) return null;
    const j = (await r.json()) as unknown;
    const list = Array.isArray(j) ? j : ((j as { jobs?: unknown[] })?.jobs ?? null);
    return Array.isArray(list) ? list.length : null;
  } catch {
    return null;
  }
}

/** Find each company's public ATS board. Budget: at most 40 probes per call (Workers free plan allows 50 subrequests). */
export async function resolveBoards(entries: string[]): Promise<{ found: BoardHit[]; notFound: string[] }> {
  const found: BoardHit[] = [];
  const notFound: string[] = [];
  let budget = 40;
  for (const raw of entries.map((e) => e.trim()).filter(Boolean).slice(0, 12)) {
    const [namePart, urlPart] = raw.includes("http") ? [raw.split(/\s+https?:/)[0].trim(), raw.slice(raw.indexOf("http"))] : [raw, ""];
    const parsed = urlPart ? parseBoardUrl(urlPart) : null;
    const company = namePart && !/^https?:/.test(namePart) ? namePart : parsed?.slug ?? raw;
    if (parsed) {
      budget--;
      const n = await probe(parsed.vendor, parsed.slug);
      if (n != null) found.push({ company, ...parsed, jobs: n });
      else notFound.push(raw);
      continue;
    }
    let hit: BoardHit | null = null;
    outer: for (const slug of slugCandidates(company)) {
      for (const vendor of ["ashby", "greenhouse", "lever", "workable"]) {
        if (budget-- <= 0) break outer;
        const n = await probe(vendor, slug);
        if (n != null) {
          hit = { company, vendor, slug, jobs: n };
          break outer;
        }
      }
    }
    if (hit) found.push(hit);
    else notFound.push(raw);
  }
  return { found, notFound };
}

const COMPANY_TYPE_PATTERNS: Record<string, string[]> = {
  consultancies: ["consulting", "consultancy", "consultants", "advisory", "Accenture", "Deloitte", "PwC", "KPMG", "EY", "Capgemini", "McKinsey", "BCG", "Bain"],
  it_services: ["outsourcing", "nearshore", "offshore", "IT services", "Infosys", "Wipro", "TCS", "Cognizant", "HCLTech"],
  agencies: ["recruitment", "recruiting", "staffing", "talent solutions", "headhunt"],
};

export type WizardAnswers = {
  tiers?: { T1?: string; T2?: string; T3?: string; T4?: string };
  seniority?: string;
  focus?: string;
  keywords?: string;
  keywords_exclude?: string;
  industries?: string;
  home_city?: string;
  home_country?: string;
  home_region?: string;
  locations?: string;
  remote_policy?: "remote_only" | "remote_or_hybrid" | "any";
  work_authorization?: string;
  avoid?: Record<string, "exclude" | "flag" | "">;
  exclude_companies?: string;
  salary_amount?: number;
  salary_currency?: string;
  characteristics?: string;
  min_score?: number;
  rubric?: Partial<Record<"tier" | "remote" | "focus" | "seniority" | "company", number>>;
};

const list = (s: unknown) =>
  String(s ?? "")
    .split(/[\n,;]+/)
    .map((x) => x.trim())
    .filter(Boolean);

/** Build the stored profile (preferences + rubric + filters) and the free-text candidate profile from wizard answers. */
export function profileFromAnswers(a: WizardAnswers, current: PreferenceProfile): { profile: PreferenceProfile; candidate: string } {
  const tiers = { T1: list(a.tiers?.T1), T2: list(a.tiers?.T2), T3: list(a.tiers?.T3), T4: list(a.tiers?.T4) };
  const exclude: string[] = list(a.exclude_companies);
  const flag: string[] = [];
  const avoidTypes: string[] = [];
  const flagTypes: string[] = [];
  for (const [type, mode] of Object.entries(a.avoid ?? {})) {
    const pats = COMPANY_TYPE_PATTERNS[type] ?? [];
    const label = type.replace("_", " ");
    if (mode === "exclude") {
      exclude.push(...pats);
      avoidTypes.push(label);
    } else if (mode === "flag") {
      flag.push(...pats);
      flagTypes.push(label);
    }
  }
  const home = { city: String(a.home_city ?? "").trim(), country: String(a.home_country ?? "").trim(), region: String(a.home_region ?? "").trim() };
  const remotePolicy = a.remote_policy ?? "remote_only";
  const profile = normalizeProfile({
    ...current,
    roles: [...tiers.T1, ...tiers.T2, ...tiers.T3, ...tiers.T4],
    title_tiers: tiers,
    seniority: list(a.seniority),
    focus: String(a.focus ?? "").trim(),
    keywords: list(a.keywords),
    keywords_exclude: list(a.keywords_exclude),
    industries: list(a.industries),
    locations: [...new Set([home.city, home.country, ...list(a.locations)].filter(Boolean))],
    home_base: home,
    remote: remotePolicy !== "any",
    remote_policy: remotePolicy,
    work_authorization: String(a.work_authorization ?? "").trim(),
    exclude_companies: [...new Set(exclude)],
    flag_companies: [...new Set(flag)],
    company_types_avoid: avoidTypes,
    company_types_flag: flagTypes,
    salary_floor: { amount: Number(a.salary_amount) || 0, currency: String(a.salary_currency || "EUR").toUpperCase(), period: "year" },
    target_comp: Number(a.salary_amount) ? `${a.salary_amount} ${String(a.salary_currency || "EUR").toUpperCase()} / year` : current.target_comp,
    notes: String(a.characteristics ?? "").trim(),
    min_score: a.min_score ?? current.min_score ?? 50,
    rubric: a.rubric ?? current.rubric,
  });
  const lines = [
    "# What I am looking for",
    tiers.T1.length ? `- Best-fit roles (tier 1): ${tiers.T1.join(", ")}` : "",
    tiers.T2.length ? `- Good roles (tier 2): ${tiers.T2.join(", ")}` : "",
    tiers.T3.length || tiers.T4.length ? `- Acceptable roles: ${[...tiers.T3, ...tiers.T4].join(", ")}` : "",
    profile.seniority.length ? `- Seniority: ${profile.seniority.join(", ")}` : "",
    profile.focus ? `- The work should centre on: ${profile.focus}` : "",
    profile.industries.length ? `- Industries: ${profile.industries.join(", ")}` : "",
    home.city || home.country ? `- Based in ${[home.city, home.country].filter(Boolean).join(", ")}${home.region ? ` (${home.region})` : ""}` : "",
    `- Location rule: ${remotePolicy === "remote_only" ? "fully remote only" : remotePolicy === "remote_or_hybrid" ? `remote, or hybrid near ${home.city || "home"}` : "any location"}`,
    profile.work_authorization ? `- Right to work: ${profile.work_authorization}` : "",
    avoidTypes.length ? `- Avoid: ${avoidTypes.join(", ")}` : "",
    flagTypes.length ? `- Flag: ${flagTypes.join(", ")}` : "",
    profile.salary_floor.amount ? `- Minimum salary: ${profile.salary_floor.amount} ${profile.salary_floor.currency} a year` : "",
    profile.notes ? `\n## Other characteristics\n${profile.notes}` : "",
  ];
  return { profile, candidate: lines.filter(Boolean).join("\n") };
}

/** Handles /api/setup/*. Returns null for other paths. */
export async function handleSetup(request: Request, env: Env, path: string): Promise<Response | null> {
  if (!path.startsWith("/setup/")) return null;
  const configured = supabaseConfigured(env);

  if (path === "/setup/status" && request.method === "GET") {
    const supa = configured
      ? await testSupabase(env.SUPABASE_URL, env.SUPABASE_ANON_KEY, env.SUPABASE_SERVICE_ROLE_KEY ?? "")
      : null;
    const owner = supa?.schema.ok ? await getOwnerId(env) : null;
    const { user, pending } = supa?.schema.ok ? await currentUser(request, env) : { user: null, pending: [] };
    const isOwner = Boolean(user && owner && user.id === owner);
    let llm: { configured: boolean; provider?: string; model?: string; baseUrl?: string } = { configured: false };
    let onboarded = false;
    if (isOwner && user) {
      const s = await loadLlmSettings(env, user.id);
      llm = s ? { configured: true, provider: s.provider, model: s.model, baseUrl: s.provider === "custom" ? s.baseUrl : "" } : { configured: false };
      const prof = await loadProfile(createServiceSupabase(env), user.id);
      onboarded = Boolean(prof.onboarded_at);
    }
    let projectRef = "";
    try {
      const host = new URL(env.SUPABASE_URL).hostname;
      projectRef = host.endsWith(".supabase.co") ? host.split(".")[0] : "";
    } catch {
      /* not configured */
    }
    return jsonResponse(
      {
        supabase: { configured, url: configured ? env.SUPABASE_URL : "", projectRef, checks: supa },
        encKey: encKeyOk(env),
        owner: Boolean(owner),
        signedIn: Boolean(user),
        email: isOwner ? user?.email ?? "" : "",
        isOwner,
        setupTokenRequired: Boolean(env.SETUP_TOKEN) && !owner,
        llm,
        onboarded,
      },
      {},
      pending,
    );
  }

  if (path === "/setup/test-supabase" && request.method === "POST") {
    // Only useful before Supabase is configured: lets the wizard check pasted credentials. Nothing is stored.
    if (configured && (await getOwnerId(env))) return jsonResponse({ error: "Already configured" }, { status: 409 });
    const b = (await request.json().catch(() => ({}))) as { url?: string; anonKey?: string; serviceKey?: string };
    return jsonResponse({ checks: await testSupabase(String(b.url ?? ""), String(b.anonKey ?? ""), String(b.serviceKey ?? "")) });
  }

  if (!configured) return jsonResponse({ error: "Supabase is not configured yet" }, { status: 412 });
  const { user, pending } = await currentUser(request, env);
  if (!user) return jsonResponse({ error: "Sign in first" }, { status: 401 }, pending);
  const owner = await getOwnerId(env);

  if (path === "/setup/claim" && request.method === "POST") {
    if (owner) return jsonResponse({ ok: owner === user.id, owner: true }, { status: owner === user.id ? 200 : 409 }, pending);
    if (env.SETUP_TOKEN) {
      const b = (await request.clone().json().catch(() => ({}))) as { token?: string };
      if (String(b.token ?? "") !== env.SETUP_TOKEN) return jsonResponse({ error: "Wrong setup token" }, { status: 403 }, pending);
    }
    const { error } = await createServiceSupabase(env).from("jobkit_instance").insert({ id: 1, owner_user_id: user.id });
    if (error) return jsonResponse({ error: error.message }, { status: 409 }, pending);
    return jsonResponse({ ok: true }, {}, pending);
  }

  if (!owner || owner !== user.id) return jsonResponse({ error: "Only the owner can change setup" }, { status: 403 }, pending);
  const admin: SupabaseClient = createServiceSupabase(env);

  if (path === "/setup/llm" && request.method === "POST") {
    if (!encKeyOk(env)) return jsonResponse({ error: "Set SETTINGS_ENC_KEY on the Worker first (see step 1)" }, { status: 412 }, pending);
    const b = (await request.json().catch(() => ({}))) as { provider?: string; baseUrl?: string; model?: string; apiKey?: string };
    const provider = (String(b.provider ?? "openrouter") as LlmProvider) in PROVIDERS ? (String(b.provider) as LlmProvider) : "openrouter";
    const baseUrl = provider === "custom" ? cleanUrl(String(b.baseUrl ?? "")) : PROVIDERS[provider].baseUrl;
    if (!/^https?:\/\//.test(baseUrl)) return jsonResponse({ error: "Enter the API base URL (ending in /v1)" }, { status: 400 }, pending);
    let apiKey = String(b.apiKey ?? "").trim();
    if (!apiKey) {
      const existing = await loadLlmSettings(env, user.id);
      if (!existing) return jsonResponse({ error: "Paste an API key" }, { status: 400 }, pending);
      apiKey = existing.apiKey;
    }
    const test = await testLlmKey({ provider, baseUrl, model: "", apiKey });
    if (!test.ok) return jsonResponse({ error: test.detail, test }, { status: 400 }, pending);
    const { error } = await admin.from("jobkit_settings").upsert(
      {
        user_id: user.id,
        llm_provider: provider,
        llm_base_url: provider === "custom" ? baseUrl : "",
        llm_model: String(b.model ?? "").trim(),
        llm_key_enc: await encryptSecret(env, apiKey),
        updated_at: new Date().toISOString(),
      },
      { onConflict: "user_id" },
    );
    if (error) throw error;
    return jsonResponse({ ok: true, test }, {}, pending);
  }

  if (path === "/setup/resolve-boards" && request.method === "POST") {
    const b = (await request.json().catch(() => ({}))) as { companies?: string[] };
    return jsonResponse(await resolveBoards(Array.isArray(b.companies) ? b.companies.map(String) : []), {}, pending);
  }

  if (path === "/setup/preferences" && request.method === "GET") {
    const prof = await loadProfile(admin, user.id);
    const { data: targets } = await admin.from("jobkit_scan_targets").select("company, vendor, slug, enabled").eq("user_id", user.id).order("company");
    return jsonResponse({ profile: prof, targets: targets ?? [] }, {}, pending);
  }

  if (path === "/setup/preferences" && request.method === "POST") {
    const b = (await request.json().catch(() => ({}))) as { answers?: WizardAnswers; boards?: BoardHit[] };
    const current = await loadProfile(admin, user.id);
    const { profile, candidate } = profileFromAnswers(b.answers ?? {}, current);
    profile.companies = [...new Set((b.boards ?? []).map((x) => String(x.company)))];
    await saveProfile(admin, user.id, profile);
    await admin.from("jobkit_preference_profile").update({ candidate_profile: candidate }).eq("user_id", user.id);
    const boards = (b.boards ?? []).filter((x) => x.vendor && x.slug).slice(0, 60);
    if (boards.length) {
      const { error } = await admin.from("jobkit_scan_targets").upsert(
        boards.map((x) => ({ user_id: user.id, id: `${x.vendor}-${x.slug}`, company: String(x.company), vendor: x.vendor, slug: x.slug, enabled: true })),
        { onConflict: "user_id,id" },
      );
      if (error) throw error;
    }
    return jsonResponse({ ok: true, profile, candidate }, {}, pending);
  }

  if (path === "/setup/finish" && request.method === "POST") {
    const prof = await loadProfile(admin, user.id);
    await saveProfile(admin, user.id, { ...prof, onboarded_at: new Date().toISOString() });
    return jsonResponse({ ok: true }, {}, pending);
  }

  return jsonResponse({ error: "Not found" }, { status: 404 }, pending);
}
