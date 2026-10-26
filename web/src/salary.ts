import type { Db } from "./db/types";
import type { Env, SalaryPayload } from "./types";
import { completeChat, type ChatMessage } from "./llm";

// Number runs are capped at 24 characters: unbounded runs made matching quadratic
// on long stretches of digits and spaces in posting text.
const SALARY_RE =
  /(?:€|EUR|\$|USD|£|GBP)\s*[\d][\d.,\s]{0,24}(?:k|K)?|\d[\d.,\s]{0,24}(?:€|EUR|\$|USD|£|GBP)(?:\s*\/\s*(?:year|ano|month|mês))?|[\d][\d.,\s]{0,24}(?:-|–|to|a)\s*[\d][\d.,\s]{0,24}(?:€|EUR)/gi;

const PAY_CONTEXT = /salary|compensation|pay range|base pay|\bote\b|annual|per year|salário|remuneração|vencimento|gehalt/i;

export function extractPublishedSalary(text: string): SalaryPayload["published"] | null {
  const found: { raw: string; nums: number[] }[] = [];
  for (const m of text.matchAll(SALARY_RE)) {
    const idx = m.index ?? 0;
    const ctx = text.slice(Math.max(0, idx - 160), idx + m[0].length + 60);
    if (!PAY_CONTEXT.test(ctx)) continue;
    const nums = [...m[0].matchAll(/(\d[\d.,]*\s*[kK]?)/g)]
      .map((x) => parseAmount(x[1]))
      .filter((n) => n >= 10000 && n <= 2_000_000);
    if (nums.length) found.push({ raw: m[0].trim(), nums });
    if (found.length >= 3) break;
  }
  if (!found.length) return null;
  const raw = found.map((f) => f.raw).join("; ");
  const nums = found.flatMap((f) => f.nums).sort((x, y) => x - y);
  return {
    min: nums[0],
    max: nums[nums.length - 1],
    currency: guessCurrency(raw),
    period: /month|mês/i.test(raw) ? "month" : "year",
    raw,
  };
}

/** One web-grounded LLM call that estimates several role/location pay bands at once (keeps Worker subrequests low). */
export async function estimateSalariesBatch(
  env: Env,
  userId: string,
  request: Request | null,
  items: { role: string; seniority: string; location: string }[],
  db: Db,
): Promise<{ estimates: Map<string, NonNullable<SalaryPayload["estimate"]>>; costUsd: number }> {
  const estimates = new Map<string, NonNullable<SalaryPayload["estimate"]>>();
  const keys = [...new Set(items.map((i) => salaryCacheKey(i.role, i.seniority, i.location)))];
  if (!keys.length) return { estimates, costUsd: 0 };
  const { data: cached } = await db.from("jobkit_salary_cache").select("cache_key, payload").in("cache_key", keys);
  for (const c of cached ?? []) estimates.set(String(c.cache_key), c.payload as NonNullable<SalaryPayload["estimate"]>);
  const missing = items.filter((i, n, arr) => {
    const k = salaryCacheKey(i.role, i.seniority, i.location);
    return !estimates.has(k) && arr.findIndex((x) => salaryCacheKey(x.role, x.seniority, x.location) === k) === n;
  });
  if (!missing.length) return { estimates, costUsd: 0 };
  const result = await completeChat(env, {
    request,
    userId,
    model: "openai/gpt-4o-mini:online",
    json: true,
    messages: [
      {
        role: "system",
        content:
          'You estimate market salary bands from public web references (levels.fyi, Glassdoor, Indeed, recruiter salary guides, salary surveys). For each item return gross annual values in the local currency of the location. Reply with JSON only: {"items":[{"i":number,"min":number,"median":number,"max":number,"currency":string,"period":"year","confidence":"low"|"medium"|"high","sources":[{"title":string,"url":string}]}]}. Include 2-4 real sources with URLs.',
      },
      { role: "user", content: JSON.stringify(missing.map((m, i) => ({ i, ...m }))) },
    ],
  });
  let parsed: { items?: Record<string, unknown>[] } = {};
  try {
    parsed = JSON.parse(result.content);
  } catch {
    parsed = {};
  }
  const rows: Record<string, unknown>[] = [];
  for (const it of parsed.items ?? []) {
    const m = missing[Number(it.i)];
    if (!m) continue;
    const est: NonNullable<SalaryPayload["estimate"]> = {
      min: Number(it.min) || 0,
      median: Number(it.median) || 0,
      max: Number(it.max) || 0,
      currency: String(it.currency ?? "EUR"),
      period: it.period === "month" ? "month" : "year",
      confidence: (["low", "medium", "high"].includes(String(it.confidence)) ? it.confidence : "low") as "low" | "medium" | "high",
      sources: Array.isArray(it.sources)
        ? (it.sources as { title?: string; url?: string }[])
            .filter((x) => x && /^https?:\/\//.test(String(x.url ?? "")) && !/ashbyhq|lever\.co|greenhouse\.io|jobgether|arbeitnow|remotive/i.test(String(x.url)))
            .slice(0, 4)
            .map((x) => ({ title: String(x.title ?? x.url), url: String(x.url) }))
        : [],
    };
    if (!est.median && !est.max) continue;
    const k = salaryCacheKey(m.role, m.seniority, m.location);
    estimates.set(k, est);
    rows.push({ cache_key: k, role_norm: m.role, seniority_norm: m.seniority, location_norm: m.location, payload: est, updated_at: new Date().toISOString() });
  }
  if (rows.length) await db.from("jobkit_salary_cache").upsert(rows, { onConflict: "cache_key" });
  return { estimates, costUsd: result.costUsd };
}

function parseAmount(s: string): number {
  let cleaned = s.trim();
  const k = /k/i.test(cleaned);
  cleaned = cleaned.replace(/[^\d.,]/g, "");
  if (cleaned.includes(",") && cleaned.includes(".")) {
    cleaned = cleaned.replace(/\./g, "").replace(",", ".");
  } else if (cleaned.includes(",")) {
    const parts = cleaned.split(",");
    cleaned = parts.length === 2 && parts[1].length === 3 ? parts.join("") : cleaned.replace(",", ".");
  }
  const n = parseFloat(cleaned);
  if (!Number.isFinite(n)) return 0;
  if (n < 500 && k) return n * 1000;
  return n;
}

function guessCurrency(raw: string): string {
  if (/€|EUR/i.test(raw)) return "EUR";
  if (/£|GBP/i.test(raw)) return "GBP";
  if (/\$|USD/i.test(raw)) return "USD";
  return "EUR";
}

export function salaryCacheKey(role: string, seniority: string, location: string): string {
  const norm = (x: string) =>
    x
      .normalize("NFD")
      .replace(/\p{M}/gu, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");
  return `${norm(role)}|${norm(seniority)}|${norm(location)}`;
}

export async function loadSalaryCache(
  admin: Db,
  key: string,
): Promise<SalaryPayload["estimate"] | null> {
  const { data } = await admin
    .from("jobkit_salary_cache")
    .select("payload")
    .eq("cache_key", key)
    .maybeSingle();
  if (!data?.payload) return null;
  const p = data.payload as SalaryPayload["estimate"];
  return p ?? null;
}

export async function saveSalaryCache(
  admin: Db,
  key: string,
  role: string,
  seniority: string,
  location: string,
  estimate: NonNullable<SalaryPayload["estimate"]>,
): Promise<void> {
  await admin.from("jobkit_salary_cache").upsert(
    {
      cache_key: key,
      role_norm: role,
      seniority_norm: seniority,
      location_norm: location,
      payload: estimate,
      updated_at: new Date().toISOString(),
    },
    { onConflict: "cache_key" },
  );
}

export async function estimateSalary(
  env: Env,
  userId: string,
  request: Request | null,
  role: string,
  seniority: string,
  location: string,
  admin: Db,
): Promise<{ estimate: NonNullable<SalaryPayload["estimate"]>; costUsd: number }> {
  const key = salaryCacheKey(role, seniority, location);
  const cached = await loadSalaryCache(admin, key);
  if (cached) return { estimate: cached, costUsd: 0 };

  const messages: ChatMessage[] = [
    {
      role: "system",
      content:
        "You estimate market salaries for a role and location. Use web references (levels.fyi, Glassdoor, Indeed, recruiter salary guides, salary surveys). Reply with JSON only: {min, median, max, currency, period: \"year\"|\"month\", confidence: \"low\"|\"medium\"|\"high\", sources: [{title, url}]}. Gross annual values in the local currency of the location.",
    },
    {
      role: "user",
      content: JSON.stringify({ role, seniority, location }),
    },
  ];
  const result = await completeChat(env, {
    request,
    userId,
    messages,
    model: "openai/gpt-4o-mini:online",
    json: true,
  });
  let estimate: NonNullable<SalaryPayload["estimate"]> = {
    min: 0,
    median: 0,
    max: 0,
    currency: "EUR",
    period: "year",
    confidence: "low",
    sources: [],
  };
  try {
    const parsed = JSON.parse(result.content) as NonNullable<SalaryPayload["estimate"]>;
    estimate = {
      min: Number(parsed.min) || 0,
      median: Number(parsed.median) || 0,
      max: Number(parsed.max) || 0,
      currency: String(parsed.currency ?? "EUR"),
      period: parsed.period === "month" ? "month" : "year",
      confidence: parsed.confidence ?? "medium",
      sources: Array.isArray(parsed.sources) ? parsed.sources : [],
    };
  } catch {
    /* keep defaults */
  }
  if (estimate.median > 0 || estimate.max > 0) {
    await saveSalaryCache(admin, key, role, seniority, location, estimate);
  }
  return { estimate, costUsd: result.costUsd };
}

const BENEFIT_CHECKS: { re: RegExp; label: string }[] = [
  { re: /health insurance/i, label: "Health insurance" },
  { re: /seguro de sa[uú]de/i, label: "Health insurance" },
  { re: /\bremote\b/i, label: "Remote" },
  { re: /\bremoto\b/i, label: "Remote" },
  { re: /\bhybrid\b/i, label: "Hybrid" },
  { re: /h[ií]brido/i, label: "Hybrid" },
  { re: /\bequity\b/i, label: "Equity" },
  { re: /stock options/i, label: "Stock options" },
  { re: /meal allowance/i, label: "Meal allowance" },
  { re: /subs[ií]dio de refei/i, label: "Meal allowance" },
  { re: /learning budget/i, label: "Learning budget" },
  { re: /formação/i, label: "Learning budget" },
];

export function extractBenefits(text: string): string[] {
  const benefits: string[] = [];
  for (const { re, label } of BENEFIT_CHECKS) {
    if (re.test(text)) benefits.push(label);
  }
  return [...new Set(benefits)].slice(0, 8);
}
