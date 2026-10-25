import type { SupabaseClient } from "@supabase/supabase-js";
import type { Env, PreferenceProfile, RawOpening, ScoreBreakdown } from "../types";
import { createServiceSupabase } from "../auth";
import { loadScoringCandidate } from "../experience";
import { classifySuitability, candidateSummary, titleVerdict, isEngineeringTitle, type Suitability } from "./suitability";
import { completeChat, type ChatMessage } from "../llm";
import { langName, tr } from "../i18n";
import { refreshProfileFromFeedback, loadProfile, loadCandidateProfile, seedScanTargets, normalizeRubric } from "../preferences";
import { dedupeOpenings, prefilterOpenings, companyExcluded, companyFlagged } from "./prefilter";
import { fetchAllSources, fetchAshby, fetchTeamtailor, remoteComOpening, remoteComJson, type ScanTarget } from "./sources";
import {
  estimateSalariesBatch,
  extractBenefits,
  extractPublishedSalary,
  salaryCacheKey,
} from "../salary";

/**
 * Cloudflare Workers cap subrequests per invocation (50 on the free plan, 1000 on paid; set SUBREQUEST_LIMIT to match).
 * About 30 go to Supabase, the LLM and the two remote-job feeds; the rest go to company boards, which rotate across runs.
 * Going over the cap makes the final writes fail, so the scan would find jobs but save none.
 */
const SCAN_OVERHEAD_SUBREQUESTS = 34;
const MAX_TARGETS_PER_RUN = 60;
export function targetsPerRun(env: Pick<Env, "SUBREQUEST_LIMIT">): number {
  const limit = Number(env.SUBREQUEST_LIMIT) || 50;
  return Math.max(4, Math.min(MAX_TARGETS_PER_RUN, limit - SCAN_OVERHEAD_SUBREQUESTS));
}
/** Default minimum score for the daily scan (profile.min_score overrides it). */
const MAX_PER_COMPANY = 5;
const MIN_SCORE = 50;

function homeLabel(profile: PreferenceProfile): string {
  const h = profile.home_base;
  return [h?.city, h?.country].filter(Boolean).join(", ") || profile.locations[0] || "";
}

/** Location words that count as "where I can work" for remote roles (home country, region, cities, "anywhere"). */
function homeTerms(profile: PreferenceProfile): string[] {
  const h = profile.home_base;
  return [h?.country, h?.region, h?.city, ...profile.locations]
    .map((x) => String(x ?? "").toLowerCase().trim())
    .filter(Boolean);
}

/** Flags shown on every match (checks worth making before applying), de-duplicated against the LLM's own. */
export function standardFlags(o: RawOpening, llm: string[], profile: PreferenceProfile): string[] {
  const out = [...llm];
  const has = (re: RegExp) => out.some((f) => re.test(f));
  const loc = o.location.toLowerCase();
  const title = o.title.toLowerCase();
  const terms = homeTerms(profile);
  const home = homeLabel(profile);
  if (home && !terms.some((t) => loc.includes(t)) && !has(/eligib|elegib/i)) out.push(`Confirm you can work remotely from ${home} (local entity, EOR or contractor)`);
  if (!has(/non-compete|compet|concorr/i)) out.push("Check the non-compete / outside-activities clause");
  for (const f of profile.custom_flags ?? []) {
    if (!f?.label || out.includes(f.label)) continue;
    let applies = true;
    if (f.title) {
      try {
        applies = new RegExp(f.title, "i").test(title);
      } catch {
        applies = false;
      }
    }
    if (applies) out.push(f.label);
  }
  return out.slice(0, 6);
}

export type ScanResult = {
  inserted: number;
  targets?: number;
  fetched: number;
  scored: number;
  costUsd: number;
  runId?: number;
  insertErrors?: string[];
  runLogError?: string;
};

function slugId(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80);
}

/** The rubric lives in the profile (weights + focus text), so each user scores jobs against their own criteria. */
export function rubricText(profile: PreferenceProfile): string {
  const w = normalizeRubric(profile.rubric);
  const home = homeLabel(profile) || "the candidate's country";
  const city = profile.home_base?.city || "the candidate's city";
  const remoteRule =
    profile.remote_policy === "any"
      ? `remote: fully remote and open to ${home} = ${w.remote}; hybrid or on-site in/near ${city} = ${w.remote}; elsewhere = ${Math.round(w.remote / 2)}.`
      : profile.remote_policy === "remote_or_hybrid"
        ? `remote: fully remote and open to ${home} = ${w.remote}; hybrid near ${city} = ${Math.round(w.remote / 2)}; anything else = 0.`
        : `remote: fully remote and open to ${home} = ${w.remote}; anything else = 0.`;
  const focus = profile.focus?.trim() || (profile.keywords.length ? profile.keywords.join(", ") : "the candidate's stated interests");
  const avoid = [...(profile.company_types_avoid ?? []), ...(profile.company_types_flag ?? [])];
  return `Rubric 0-100 (sum of the parts):
- tier (role): T1 = ${w.tier}, T2 = ${Math.round(w.tier * 0.8)}, T3 = ${Math.round(w.tier * 0.53)}, T4 = ${Math.round(w.tier * 0.33)}, outside the tiers = 0. Use the profile's titles per tier.
- ${remoteRule}
- focus 0-${w.focus}: how central "${focus}" is to the role's actual mandate (core of the job = high; mentioned in passing = low).
- seniority_scope 0-${w.seniority}: seniority (${profile.seniority.join(", ") || "as in the profile"}) and scope.
- company 0-${w.company}: product company / the kind of company the candidate wants = high; ${avoid.length ? avoid.join(", ") : "consultancies and IT-services/outsourcing firms"} = low or 0.
Flags (short labels, only those that apply or need checking): remote eligibility, non-compete, unclear mandate, company type to avoid${(profile.custom_flags ?? []).length ? ", " + (profile.custom_flags ?? []).map((f) => `"${f.label}"`).join(", ") : ""}.${profile.rubric_notes?.trim() ? `\nExtra guidance from the candidate:\n${profile.rubric_notes.trim()}` : ""}`;
}

/** Sentences about where the role can be done; these usually sit at the end of a posting, past the excerpt. */
function locationHints(text: string): string {
  const re = /remote|anywhere|location|time ?zones?|hybrid|on-?site|relocat|europe|emea|globally|worldwide|office/i;
  return text
    .split(/(?<=[.!?])\s+|\n+/)
    .filter((x) => re.test(x))
    .map((x) => x.trim().slice(0, 220))
    .slice(0, 6)
    .join(" | ");
}

/** Remote that clearly includes the candidate's home: global/anywhere, or a region/country from the profile. */
function clearlyRemoteForHome(o: RawOpening, profile: PreferenceProfile): boolean {
  const loc = o.location.toLowerCase();
  const text = o.description.toLowerCase();
  const terms = homeTerms(profile);
  const wide = (x: string) => /global|worldwide|anywhere/.test(x) || terms.some((t) => t.length > 1 && x.includes(t));
  if (/remot/.test(loc) && wide(loc)) return true;
  return /(executed|work|done|based) from anywhere|we hire globally|fully remote[^.]{0,60}(global|anywhere)/.test(text);
}

export async function scoreOpenings(
  env: Env,
  userId: string,
  request: Request | null,
  profile: PreferenceProfile,
  candidateProfile: string,
  openings: RawOpening[],
): Promise<{
  scores: { score: number; reason: string; breakdown: ScoreBreakdown | null; flags: string[] }[];
  costUsd: number;
}> {
  const w = normalizeRubric(profile.rubric);
  const liked = (profile.liked_examples ?? []).slice(0, 3);
  const messages: ChatMessage[] = [
    {
      role: "system",
      content:
        "You are a recruiter scoring job postings for one specific candidate. Apply the rubric strictly and return the breakdown. " +
        rubricText(profile) +
        '\nReply with JSON only: {"items":[{"i":number,"tier":"T1"|"T2"|"T3"|"T4"|"-","remote":"total"|"hybrid"|"other","remote_points":number,"focus":number,"seniority_scope":number,"company_type":string,"company_points":number,"flags":string[],"reason":string}]} with one item per job. reason: one short, direct sentence in ' + langName(env) + '; flags in the same language.',
    },
    {
      role: "user",
      content: JSON.stringify({
        candidate: candidateProfile.slice(0, 12000),
        preferences: {
          title_tiers: profile.title_tiers,
          seniority: profile.seniority,
          locations: profile.locations,
          home_base: profile.home_base,
          remote_policy: profile.remote_policy,
          industries: profile.industries,
          keywords: profile.keywords,
          focus: profile.focus,
        },
        liked_examples_score_high: liked,
        jobs: openings.map((o, i) => ({
          i,
          company: o.company,
          title: o.title,
          location: o.location,
          flagged_company_type: companyFlagged(o.company, profile),
          excerpt: o.description.slice(0, 900),
          location_hints: locationHints(o.description),
        })),
      }),
    },
  ];
  const result = await completeChat(env, { request, userId, messages, model: "google/gemini-2.5-flash", json: true });
  const scores = openings.map(() => ({ score: 0, reason: tr(env, "Score unavailable."), breakdown: null as ScoreBreakdown | null, flags: [] as string[] }));
  const clamp = (v: unknown, max: number) => Math.round(Math.min(max, Math.max(0, Number(v) || 0)));
  try {
    const parsed = JSON.parse(result.content) as { items?: Record<string, unknown>[] };
    for (const it of parsed.items ?? []) {
      const i = Number(it.i);
      if (!(i >= 0 && i < scores.length)) continue;
      const tierPts: Record<string, number> = { T1: w.tier, T2: Math.round(w.tier * 0.8), T3: Math.round(w.tier * 0.53), T4: Math.round(w.tier * 0.33) };
      const tier = String(it.tier ?? "-");
      const remote = String(it.remote ?? "other");
      const breakdown: ScoreBreakdown = {
        tier,
        tier_points: tierPts[tier] ?? 0,
        remote,
        remote_points: remote === "total" ? w.remote : remote.startsWith("hybrid") ? clamp(it.remote_points, w.remote) : 0,
        focus: clamp(it.focus ?? it.ai_centrality, w.focus),
        seniority_scope: clamp(it.seniority_scope, w.seniority),
        company_type: String(it.company_type ?? ""),
        company_points: clamp(it.company_points, w.company),
      };
      if (breakdown.remote_points < w.remote && clearlyRemoteForHome(openings[i], profile)) {
        breakdown.remote = "total";
        breakdown.remote_points = w.remote;
      }
      if (companyFlagged(openings[i].company, profile)) breakdown.company_points = Math.min(breakdown.company_points, Math.round(w.company / 5));
      const total = breakdown.tier_points + breakdown.remote_points + breakdown.focus + breakdown.seniority_scope + breakdown.company_points;
      const flags = Array.isArray(it.flags) ? it.flags.map(String).filter(Boolean).slice(0, 6) : [];
      if (companyFlagged(openings[i].company, profile) && !flags.some((f) => /company type|outsourc|services/i.test(f))) {
        flags.push(tr(env, "Company type you asked to flag"));
      }
      scores[i] = {
        // With a remote policy, a remote score of 0 means "not eligible": cap the total.
        score: profile.remote_policy !== "any" && breakdown.remote_points === 0 ? Math.min(total, 30) : total,
        reason: String(it.reason ?? "").slice(0, 280),
        breakdown,
        flags,
      };
    }
  } catch {
    /* keep defaults */
  }
  return { scores, costUsd: result.costUsd };
}

export async function runJobScan(
  env: Env,
  supabase: SupabaseClient,
  userId: string,
  trigger: "cron" | "manual",
  request: Request | null,
): Promise<ScanResult> {
  let costUsd = 0;

  const { count: fbCount } = await supabase
    .from("jobkit_job_feedback")
    .select("job_id", { count: "exact", head: true })
    .eq("user_id", userId);
  if ((fbCount ?? 0) > 0) {
    await refreshProfileFromFeedback(env, supabase, userId, request)
      .then((r) => {
        costUsd += r.costUsd;
      })
      .catch(() => {});
  }

  const profile = await loadProfile(supabase, userId);
  await seedScanTargets(supabase, userId, profile);

  const { data: targetsRaw } = await supabase
    .from("jobkit_scan_targets")
    .select("id, company, vendor, slug, enabled, last_scanned_at")
    .eq("user_id", userId)
    .eq("enabled", true)
    .order("last_scanned_at", { ascending: true, nullsFirst: true })
    .limit(targetsPerRun(env));
  const targets: ScanTarget[] = (targetsRaw ?? []).map((t) => ({
    company: String(t.company),
    vendor: String(t.vendor),
    slug: String(t.slug),
  }));

  const fetchedAll = await fetchAllSources(env, targets);
  if (targetsRaw?.length) {
    const now = new Date().toISOString();
    await supabase
      .from("jobkit_scan_targets")
      .update({ last_scanned_at: now })
      .eq("user_id", userId)
      .in("id", targetsRaw.map((t) => String(t.id)));
  }
  const candidateProfile = await loadScoringCandidate(supabase, userId, await loadCandidateProfile(supabase, userId));
  const excludedCompanies = fetchedAll.filter((o) => companyExcluded(o.company, profile)).length;
  const prefFiltered = prefilterOpenings(
    fetchedAll.filter((o) => !companyExcluded(o.company, profile)),
    profile,
  );

  const { data: existingJobs } = await supabase
    .from("jobkit_jobs")
    .select("external_key, posting")
    .eq("user_id", userId);
  const { data: existingApps } = await supabase
    .from("jobkit_apps")
    .select("posting")
    .eq("user_id", userId);

  const keys = new Set(
    (existingJobs ?? []).map((j) => String(j.external_key ?? "")).filter(Boolean),
  );
  const urls = new Set<string>();
  for (const j of existingJobs ?? []) {
    if (j.posting) urls.add(String(j.posting).split("?")[0].toLowerCase());
  }
  for (const a of existingApps ?? []) {
    if (a.posting) urls.add(String(a.posting).split("?")[0].toLowerCase());
  }

  const perCompany = new Map<string, number>();
  const candidates = dedupeOpenings(prefFiltered, keys, urls)
    .filter((o) => !(profile.exclude_engineering_titles && isEngineeringTitle(o.title)))
    // Cap per company so one large board can't crowd out the others in the scoring batch.
    .filter((o) => {
      const k = o.company.toLowerCase();
      const n = (perCompany.get(k) ?? 0) + 1;
      perCompany.set(k, n);
      return n <= MAX_PER_COMPANY;
    })
    .slice(0, 30);
  const scored: { opening: RawOpening; score: number; reason: string; breakdown: ScoreBreakdown | null; flags: string[] }[] = [];
  if (candidates.length) {
    const { scores, costUsd: c } = await scoreOpenings(env, userId, request, profile, candidateProfile, candidates);
    costUsd += c;
    candidates.forEach((opening, i) => scored.push({ opening, ...scores[i] }));
  }

  scored.sort((a, b) => b.score - a.score);
  const top = scored.filter((s) => s.score >= (profile.min_score ?? MIN_SCORE)).slice(0, 10);
  for (const t of top) t.flags = standardFlags(t.opening, t.flags, profile).map((f) => tr(env, f));
  let suit: Suitability[] = [];
  if (top.length && (profile.role_constraints ?? []).length) {
    const r = await classifySuitability(env, userId, request, profile, await candidateSummary(supabase, userId), top.map((t) => ({ title: t.opening.title, company: t.opening.company, text: t.opening.description })));
    suit = r.results;
    costUsd += r.costUsd;
  }

  const salaryDb = env.SUPABASE_SERVICE_ROLE_KEY ? createServiceSupabase(env) : supabase;
  const seniority = profile.seniority[0] ?? "Senior";
  const prepared = top.map((item, n) => {
    const published = extractPublishedSalary(item.opening.description);
    const benefits = extractBenefits(item.opening.description);
    const loc = item.opening.location || homeLabel(profile) || "Remote";
    return { ...item, published, benefits, loc, suitability: suit[n] ?? null };
  });
  let estimates = new Map<string, unknown>();
  try {
    const need = prepared.filter((p) => !p.published).map((p) => ({ role: p.opening.title, seniority, location: p.loc }));
    const r = await estimateSalariesBatch(env, userId, request, need, salaryDb);
    estimates = r.estimates;
    costUsd += r.costUsd;
  } catch {
    /* salary is best-effort; the UI can retry per job */
  }

  const stamp = Date.now().toString(36);
  const rows = prepared.map((p, n) => ({
    user_id: userId,
    id: `${slugId(p.opening.company)}-${slugId(p.opening.title)}-${stamp}${n}`.slice(0, 120),
    company: p.opening.company,
    role: p.opening.title,
    posting: p.opening.url,
    posting_text: p.opening.description.slice(0, 50000),
    location: p.opening.location,
    source: "scan",
    external_key: p.opening.externalKey,
    scan_score: p.score,
    match_reason: p.reason,
    score_breakdown: p.breakdown ?? {},
    flags: p.flags,
    is_new: true,
    salary: p.published
      ? { published: p.published, benefits: p.benefits }
      : estimates.has(salaryCacheKey(p.opening.title, seniority, p.loc))
        ? { estimate: estimates.get(salaryCacheKey(p.opening.title, seniority, p.loc)), benefits: p.benefits }
        : { benefits: p.benefits },
    benefits: p.benefits,
    suitability: p.suitability,
  }));
  let inserted = 0;
  const insertErrors: string[] = [];
  if (rows.length) {
    const { error, count } = await supabase.from("jobkit_jobs").insert(rows, { count: "exact" });
    if (error) insertErrors.push(error.message);
    else inserted = count ?? rows.length;
  }

  const { data: runRow, error: runErr } = await supabase
    .from("jobkit_scan_runs")
    .insert({
      user_id: userId,
      trigger,
      jobs_fetched: fetchedAll.length,
      jobs_scored: scored.length,
      jobs_inserted: inserted,
      cost_usd: costUsd,
      detail: { prefFiltered: prefFiltered.length, candidates: candidates.length, excludedCompanies, targets: targets.length, insertErrors },
    })
    .select("id")
    .single();

  if (insertErrors.length || runErr) console.error("jobkit scan: could not save results", { insertErrors, runLogError: runErr?.message });

  return {
    inserted,
    targets: targets.length,
    fetched: fetchedAll.length,
    scored: scored.length,
    costUsd,
    runId: runRow?.id,
    ...(insertErrors.length ? { insertErrors } : {}),
    ...(runErr ? { runLogError: runErr.message } : {}),
  };
}

/** Resolve a posting URL (ATS link or a company careers page backed by Ashby) to one opening with full text. */
async function resolvePosting(
  url: string,
  companyFor: (vendor: string, slug: string) => string,
  teamtailorHosts: Set<string> = new Set(),
): Promise<RawOpening> {
  const u = new URL(url);
  const parts = u.pathname.split("/").filter(Boolean);
  const host = u.hostname.toLowerCase();
  const gh = /(^|\.)greenhouse\.io$/.test(host) ? parts.indexOf("jobs") : -1;
  if (gh > 0 && parts[gh + 1]) {
    const slug = parts[gh - 1], id = parts[gh + 1];
    const res = await fetch(`https://boards-api.greenhouse.io/v1/boards/${encodeURIComponent(slug)}/jobs/${encodeURIComponent(id)}`, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`Greenhouse ${res.status}`);
    const job = (await res.json()) as Record<string, unknown>;
    const html = String(job.content ?? "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
    return {
      externalKey: `greenhouse:${slug}:${id}`,
      company: companyFor("greenhouse", slug),
      title: String(job.title ?? ""),
      url,
      location: String((job.location as { name?: string } | undefined)?.name ?? ""),
      description: html.replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim(),
      source: "import",
      vendor: "greenhouse",
    };
  }
  if (host === "apply.remote.com" && parts[0] === "jobs" && parts[1]) {
    const job = ((await remoteComJson(`https://apply.remote.com/api/public/jobs/${encodeURIComponent(parts[1])}`)) as { job?: Record<string, unknown> }).job;
    const o = job && remoteComOpening(job, companyFor("remote-com", "remote"));
    if (!o) throw new Error("Remote.com posting not found");
    return { ...o, source: "import" };
  }
  // Teamtailor career sites (<x>.teamtailor.com or a custom domain): /jobs/<numeric id>-<title>, read from the public RSS feed.
  const tt = parts[0] === "jobs" ? parts[1]?.match(/^(\d+)(-|$)/) : null;
  if (tt && (/\.teamtailor\.com$/.test(host) || teamtailorHosts.has(host))) {
    const all = await fetchTeamtailor({ company: companyFor("teamtailor", host), vendor: "teamtailor", slug: host });
    const hit = all.find((o) => o.externalKey.endsWith(":" + tt[1]));
    if (!hit) throw new Error(`Posting not found on the Teamtailor site "${host}"`);
    return { ...hit, url, source: "import" };
  }
  let slug: string, match: (o: RawOpening) => boolean;
  const uuid = u.pathname.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i)?.[0]?.toLowerCase();
  if (host === "jobs.ashbyhq.com" || host === "jobs.ashbyhq.eu") slug = parts[0];
  else slug = host.replace(/^www\.|^careers\.|^jobs\./, "").split(".")[0];
  if (uuid) match = (o) => o.externalKey.toLowerCase().endsWith(":" + uuid);
  else {
    const last = slugId(parts[parts.length - 1] ?? "");
    match = (o) => slugId(o.title) === last;
  }
  const all = await fetchAshby({ company: companyFor("ashby", slug), vendor: "ashby", slug });
  const hit = all.find(match);
  if (!hit) throw new Error(`Posting not found on the Ashby board "${slug}"`);
  return { ...hit, url, source: "import" };
}

export type ImportResult = { url: string; ok: boolean; id?: string; error?: string; score?: number };

/** Add specific postings to Jobs: full text, rubric score (no minimum), standard flags, salary, and a 👍 signal. No CV/answers. */
export async function importJobs(
  env: Env,
  supabase: SupabaseClient,
  userId: string,
  request: Request | null,
  urls: string[],
): Promise<{ results: ImportResult[]; costUsd: number }> {
  let costUsd = 0;
  const { data: targets } = await supabase.from("jobkit_scan_targets").select("company, vendor, slug").eq("user_id", userId);
  const companyFor = (vendor: string, slug: string) =>
    String((targets ?? []).find((t) => t.slug === slug && (t.vendor === vendor || vendor === "ashby"))?.company ?? "") ||
    slug.charAt(0).toUpperCase() + slug.slice(1);
  const ttHosts = new Set((targets ?? []).filter((t) => t.vendor === "teamtailor").map((t) => String(t.slug)));
  const results: ImportResult[] = [];
  const openings: RawOpening[] = [];
  for (const url of urls) {
    try {
      openings.push(await resolvePosting(url, companyFor, ttHosts));
      results.push({ url, ok: true });
    } catch (e) {
      results.push({ url, ok: false, error: e instanceof Error ? e.message : String(e) });
    }
  }
  if (!openings.length) return { results, costUsd };
  const profile = await loadProfile(supabase, userId);
  const candidateProfile = await loadScoringCandidate(supabase, userId, await loadCandidateProfile(supabase, userId));
  const { scores, costUsd: c } = await scoreOpenings(env, userId, request, profile, candidateProfile, openings);
  costUsd += c;
  const seniority = profile.seniority[0] ?? "Senior";
  const verdicts: (Suitability | null)[] = openings.map((o) => titleVerdict(profile, o.title));
  const needLlm = openings.map((o, i) => ({ o, i })).filter(({ i }) => !verdicts[i]);
  if (needLlm.length && (profile.role_constraints ?? []).length) {
    const r = await classifySuitability(env, userId, request, profile, await candidateSummary(supabase, userId), needLlm.map(({ o }) => ({ title: o.title, company: o.company, text: o.description })));
    needLlm.forEach(({ i }, n) => (verdicts[i] = r.results[n]));
    costUsd += r.costUsd;
  }
  const prepared = openings.map((o, i) => ({
    suitability: verdicts[i],
    o,
    ...scores[i],
    flags: standardFlags(o, scores[i].flags, profile).map((f) => tr(env, f)),
    published: extractPublishedSalary(o.description),
    benefits: extractBenefits(o.description),
    loc: o.location || homeLabel(profile) || "Remote",
  }));
  let estimates = new Map<string, unknown>();
  try {
    const salaryDb = env.SUPABASE_SERVICE_ROLE_KEY ? createServiceSupabase(env) : supabase;
    const need = prepared.filter((p) => !p.published).map((p) => ({ role: p.o.title, seniority, location: p.loc }));
    if (need.length) {
      const r = await estimateSalariesBatch(env, userId, request, need, salaryDb);
      estimates = r.estimates;
      costUsd += r.costUsd;
    }
  } catch {
    /* best-effort */
  }
  const { data: existing } = await supabase
    .from("jobkit_jobs")
    .select("id, external_key")
    .eq("user_id", userId)
    .in("external_key", openings.map((o) => o.externalKey));
  let k = 0;
  for (const p of prepared) {
    const id = String((existing ?? []).find((e) => e.external_key === p.o.externalKey)?.id ?? `${slugId(p.o.company)}-${slugId(p.o.title)}`.slice(0, 120));
    const est = estimates.get(salaryCacheKey(p.o.title, seniority, p.loc));
    const row = {
      user_id: userId,
      id,
      company: p.o.company,
      role: p.o.title,
      posting: p.o.url,
      posting_text: p.o.description.slice(0, 50000),
      location: p.o.location,
      source: "import",
      external_key: p.o.externalKey,
      scan_score: p.score,
      match_reason: p.reason,
      score_breakdown: p.breakdown ?? {},
      flags: p.flags,
      is_new: true,
      dismissed: false,
      salary: p.published ? { published: p.published, benefits: p.benefits } : est ? { estimate: est, benefits: p.benefits } : { benefits: p.benefits },
      benefits: p.benefits,
      suitability: p.suitability,
      updated_at: new Date().toISOString(),
    };
    while (results[k] && !results[k].ok) k++;
    const r = results[k++];
    const { error } = await supabase.from("jobkit_jobs").upsert(row, { onConflict: "user_id,id" });
    if (error) {
      r.ok = false;
      r.error = error.message;
      continue;
    }
    r.id = id;
    r.score = p.score;
    const { count: ups } = await supabase
      .from("jobkit_job_feedback")
      .select("job_id", { count: "exact", head: true })
      .eq("user_id", userId)
      .eq("job_id", id)
      .eq("signal", "up");
    if (!ups) await supabase.from("jobkit_job_feedback").insert({ user_id: userId, job_id: id, signal: "up", reason: tr(env, "Added manually") });
  }
  return { results, costUsd };
}
