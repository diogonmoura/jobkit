import type { PreferenceProfile, RawOpening } from "../types";

function norm(s: string): string {
  return s
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase();
}

function haystack(o: RawOpening): string {
  return norm(`${o.title} ${o.company} ${o.location} ${o.description}`);
}

const STOP = new Set(["and", "the", "of", "de", "e", "da", "do", "ai", "/", "&", "-", "(ai)", "manager", "lead", "senior", "staff", "head", "principal"]);
/** Optional local spellings, e.g. { "munich": ["munchen"] }. Extend for your own cities if boards use both. */
const LOC_ALIASES: Record<string, string[]> = {};

function tokens(s: string): string[] {
  return norm(s)
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length > 2 && !STOP.has(t));
}

/** Cheap ranking before the LLM: hard-filter excludes and location, then rank by role/keyword overlap. */
export function prefilterOpenings(
  openings: RawOpening[],
  profile: PreferenceProfile,
): RawOpening[] {
  const excludes = profile.keywords_exclude.map(norm).filter(Boolean);
  const keywords = profile.keywords.map(norm).filter(Boolean);
  const roleTokens = [...new Set(profile.roles.flatMap(tokens))];
  const tierPhrases = Object.entries(profile.title_tiers ?? {}).flatMap(([t, list]) =>
    (list ?? []).map((ph) => ({ ph: norm(ph), w: ({ T1: 8, T2: 6, T3: 4, T4: 3 } as Record<string, number>)[t] ?? 2 })),
  );
  const home = profile.home_base;
  const places = [...profile.locations, home?.city ?? "", home?.country ?? ""].map(norm).filter(Boolean);
  const locs = [...new Set(places.flatMap((l) => [l, ...(LOC_ALIASES[l] ?? [])]))];
  const regions = [home?.region ?? "", home?.country ?? ""]
    .map(norm)
    .filter(Boolean)
    .flatMap((r) => (/europe|\beu\b/.test(r) ? [r, "europe", "emea", "eu/", "eu -", "(eu", ", eu"] : [r]));
  const anyLocation = profile.remote_policy === "any";

  const ranked: { o: RawOpening; score: number }[] = [];
  for (const o of openings) {
    const title = norm(o.title);
    if (excludes.some((x) => new RegExp(`\\b${x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(title))) continue;
    const loc = norm(o.location);
    const remoteOk =
      profile.remote &&
      /remote|remoto|anywhere|worldwide/.test(loc) &&
      (/anywhere|worldwide|global/.test(loc) ||
        regions.some((r) => loc.includes(r)) ||
        /^\s*(fully\s+)?remot[eo]\s*$/.test(loc));
    const locOk = locs.some((l) => loc.includes(l));
    if (!anyLocation && locs.length && !remoteOk && !locOk) continue;
    const h = haystack(o);
    let score = 0;
    for (const t of roleTokens) if (title.includes(t)) score += 3;
    for (const { ph, w } of tierPhrases) if (ph && title.includes(ph)) score += w;
    if (/\b(junior|jr\.?|associate|intern|internship|graduate|trainee|mid[- ]level)\b/.test(title)) continue;
    for (const k of keywords) if (h.includes(k)) score += 1;
    if (locOk) score += 1;
    if (!roleTokens.length && !keywords.length) score = 1;
    if (score > 0) ranked.push({ o, score });
  }
  ranked.sort((a, b) => b.score - a.score);
  return ranked.map((r) => r.o);
}

export function dedupeOpenings(
  openings: RawOpening[],
  existingKeys: Set<string>,
  existingUrls: Set<string>,
): RawOpening[] {
  const seen = new Set<string>();
  const out: RawOpening[] = [];
  for (const o of openings) {
    const key = o.externalKey || o.url;
    if (!key || seen.has(key)) continue;
    if (existingKeys.has(key)) continue;
    const urlNorm = o.url.split("?")[0].toLowerCase();
    if (urlNorm && existingUrls.has(urlNorm)) continue;
    seen.add(key);
    out.push(o);
  }
  return out;
}

function companyMatches(company: string, list: string[] | undefined): boolean {
  const c = norm(company);
  return (list ?? []).some((x) => {
    const n = norm(x).trim();
    return n.length > 1 && new RegExp(`(^|[^a-z0-9])${n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^a-z0-9]|$)`).test(c);
  });
}

/** Hard filter: companies (or company-name patterns) the user excluded, e.g. consultancies. */
export function companyExcluded(company: string, profile: PreferenceProfile): boolean {
  return companyMatches(company, profile.exclude_companies);
}

/** Soft flag: company-name patterns the user wants flagged, e.g. IT services / outsourcing. */
export function companyFlagged(company: string, profile: PreferenceProfile): boolean {
  return companyMatches(company, profile.flag_companies);
}
