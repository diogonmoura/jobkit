import type { SupabaseClient } from "@supabase/supabase-js";
import type { Env } from "./types";
import { completeChat } from "./llm";
import { parseBoardUrl, probe } from "./setup";

export const VENDORS = ["greenhouse", "ashby", "lever", "lever-eu", "workable", "remote-com", "teamtailor"] as const;

/** Board links embedded in a careers page (e.g. a Greenhouse embed or an "all jobs" link to Ashby/Lever/Workable). */
export function findEmbeddedBoard(html: string, host = ""): { vendor: string; slug: string } | null {
  // A Teamtailor career site on a custom domain (assets come from teamtailor-cdn.com).
  if (host && /teamtailor-cdn\.com|teamtailor\.com\/assets/i.test(html)) return { vendor: "teamtailor", slug: host.toLowerCase() };
  const h = html.replace(/&amp;/g, "&");
  const pats: [RegExp, string][] = [
    [/boards(?:-api)?\.greenhouse\.io\/(?:embed\/job_board(?:\/js)?\?for=|v1\/boards\/)([a-z0-9_-]+)/i, "greenhouse"],
    [/(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io\/([a-z0-9_-]+)/i, "greenhouse"],
    [/jobs\.ashbyhq\.com\/([a-z0-9._%-]+)/i, "ashby"],
    [/api\.ashbyhq\.com\/posting-api\/job-board\/([a-z0-9._%-]+)/i, "ashby"],
    [/jobs\.eu\.lever\.co\/([a-z0-9_-]+)/i, "lever-eu"],
    [/jobs\.lever\.co\/([a-z0-9_-]+)/i, "lever"],
    [/apply\.workable\.com\/([a-z0-9_-]+)/i, "workable"],
  ];
  for (const [re, vendor] of pats) {
    const m = h.match(re);
    if (m && !/^(embed|api|v1|j|jobs)$/i.test(m[1])) return { vendor, slug: decodeURIComponent(m[1]) };
  }
  return null;
}

const titleCase = (slug: string) => slug.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

async function boardName(vendor: string, slug: string): Promise<string> {
  if (vendor !== "greenhouse") return "";
  try {
    const r = await fetch(`https://boards-api.greenhouse.io/v1/boards/${encodeURIComponent(slug)}`, { headers: { Accept: "application/json" } });
    return r.ok ? String(((await r.json()) as { name?: string }).name ?? "") : "";
  } catch {
    return "";
  }
}

export type ResolvedBoard = { vendor: string; slug: string; company: string; jobs: number; hq: string; industry: string; costUsd: number; suggestError?: string };

/** Paste a careers or board URL: detect the ATS, test-fetch it, and suggest name/HQ/industry (cheap model, may be blank). */
export async function resolveBoardUrl(env: Env, userId: string, request: Request | null, url: string): Promise<ResolvedBoard> {
  let parsed = parseBoardUrl(url);
  let pageHost = "";
  if (!parsed) {
    let u: URL;
    try {
      u = new URL(url.trim());
    } catch {
      throw new Error("That doesn't look like a link. Paste the company's careers or job-board URL.");
    }
    pageHost = u.hostname.replace(/^(www|careers|jobs)\./, "");
    try {
      const r = await fetch(u.toString(), { headers: { "User-Agent": "Mozilla/5.0 (compatible; jobkit/1)", Accept: "text/html" }, redirect: "follow" });
      parsed = parseBoardUrl(r.url) ?? (r.ok ? findEmbeddedBoard(await r.text(), new URL(r.url || u.toString()).hostname) : null);
    } catch {
      /* fall through */
    }
  }
  if (!parsed) throw new Error("No supported job board found at that link. Supported: Greenhouse, Ashby, Lever, Workable, Teamtailor and Remote.com. Try the link to the page that lists all open jobs.");
  const jobs = await probe(parsed.vendor, parsed.slug);
  if (jobs == null) throw new Error(`Found a ${parsed.vendor} board ("${parsed.slug}") but it didn't answer. Check the link.`);
  let company = (await boardName(parsed.vendor, parsed.slug)) || titleCase(parsed.slug);
  let hq = "";
  let industry = "";
  let costUsd = 0;
  let suggestError = "";
  try {
    const r = await completeChat(env, {
      request,
      userId,
      model: "google/gemini-2.5-flash-lite",
      json: true,
      messages: [
        {
          role: "system",
          content:
            'You identify companies from their job board. Reply with JSON only: {"name":string,"hq":string,"industry":string}. name: the company\'s usual name. hq: headquarters as "City, Country" (short country, e.g. US, UK). industry: 1–3 word label (e.g. "Developer tools", "Fintech", "AI voice"). Use an empty string for anything you are not sure about; never guess.',
        },
        { role: "user", content: JSON.stringify({ board: `${parsed.vendor}/${parsed.slug}`, name_hint: company, website: pageHost }) },
      ],
    });
    costUsd = r.costUsd;
    const m = r.content.match(/\{[\s\S]*\}/);
    const j = JSON.parse(m ? m[0] : r.content) as { name?: string; hq?: string; industry?: string };
    company = String(j.name ?? "").trim().slice(0, 80) || company;
    hq = String(j.hq ?? "").trim().slice(0, 60);
    industry = String(j.industry ?? "").trim().slice(0, 40);
  } catch (e) {
    suggestError = e instanceof Error ? e.message.slice(0, 200) : String(e);
  }
  return { ...parsed, company, jobs, hq, industry, costUsd, ...(suggestError ? { suggestError } : {}) };
}

export async function addBoard(supabase: SupabaseClient, userId: string, b: { vendor: string; slug: string; company: string; hq?: string; industry?: string }) {
  if (!(VENDORS as readonly string[]).includes(b.vendor) || !b.slug || !b.company?.trim()) throw new Error("Invalid board");
  const row = { user_id: userId, id: `${b.vendor}-${b.slug}`, company: b.company.trim().slice(0, 80), vendor: b.vendor, slug: b.slug, enabled: true, hq: String(b.hq ?? "").trim().slice(0, 60), industry: String(b.industry ?? "").trim().slice(0, 40) };
  const { error } = await supabase.from("jobkit_scan_targets").upsert(row, { onConflict: "user_id,id" });
  if (error) throw error;
  return row;
}
