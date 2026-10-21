import type { Env, RawOpening } from "../types";

const UA = "jobkit-scan/1";

export type ScanTarget = {
  company: string;
  vendor: string;
  slug: string;
};

async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url, { headers: { "User-Agent": UA, Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res.json();
}

function jobId(value: unknown): string {
  if (value == null || typeof value === "boolean") return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number") return String(value);
  return "";
}

const xmlText = (s: string) =>
  s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
const tag = (item: string, name: string) => {
  const m = item.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`));
  return m ? xmlText(m[1]).trim() : "";
};

/** Parse a Teamtailor career-site RSS feed (https://<host>/jobs.rss) into openings. */
export function parseTeamtailorRss(xml: string, target: ScanTarget): RawOpening[] {
  return (xml.match(/<item>[\s\S]*?<\/item>/g) ?? [])
    .map((item) => {
      const url = tag(item, "link");
      const id = (url.match(/\/jobs\/(\d+)/) ?? [])[1] || tag(item, "guid");
      if (!id) return null;
      const locs = [...item.matchAll(/<tt:location>([\s\S]*?)<\/tt:location>/g)].map((m) =>
        [tag(m[1], "tt:city"), tag(m[1], "tt:country")].filter(Boolean).join(", ") || tag(m[1], "tt:name"),
      );
      const remote = tag(item, "remoteStatus");
      const location = [...new Set(locs.filter(Boolean))].join(" / ") + (remote && remote !== "none" ? ` (remote: ${remote})` : "");
      return {
        externalKey: `teamtailor:${target.slug}:${id}`,
        company: target.company,
        title: tag(item, "title"),
        url,
        location: location.trim(),
        description: tag(item, "description").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(),
        source: "scan",
        vendor: "teamtailor",
      } satisfies RawOpening;
    })
    .filter(Boolean) as RawOpening[];
}

export async function fetchTeamtailor(target: ScanTarget): Promise<RawOpening[]> {
  const url = `https://${target.slug.replace(/[^a-z0-9.-]/gi, "")}/jobs.rss`;
  const res = await fetch(url, { headers: { "User-Agent": UA, Accept: "application/rss+xml, application/xml" } });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return parseTeamtailorRss(await res.text(), target);
}

export async function fetchGreenhouse(
  target: ScanTarget,
): Promise<RawOpening[]> {
  const url = `https://boards-api.greenhouse.io/v1/boards/${encodeURIComponent(target.slug)}/jobs?content=true`;
  const payload = (await fetchJson(url)) as { jobs?: Record<string, unknown>[] };
  const jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
  return jobs
    .map((job) => {
      const id = jobId(job.id);
      if (!id) return null;
      const loc = job.location as { name?: string } | undefined;
      const content = String(job.content ?? "");
      return {
        externalKey: `greenhouse:${target.slug}:${id}`,
        company: target.company,
        title: String(job.title ?? ""),
        url: String(job.absolute_url ?? ""),
        location: String(loc?.name ?? ""),
        description: content.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(),
        source: "scan",
        vendor: "greenhouse",
      } satisfies RawOpening;
    })
    .filter(Boolean) as RawOpening[];
}

export async function fetchLever(target: ScanTarget): Promise<RawOpening[]> {
  const base =
    target.vendor === "lever-eu"
      ? "https://api.eu.lever.co/v0/postings"
      : "https://api.lever.co/v0/postings";
  const url = `${base}/${encodeURIComponent(target.slug)}?mode=json`;
  const payload = (await fetchJson(url)) as Record<string, unknown>[];
  if (!Array.isArray(payload)) return [];
  return payload
    .map((job) => {
      const id = jobId(job.id);
      if (!id) return null;
      const cats = (job.categories ?? {}) as { location?: string };
      const desc = String(job.descriptionPlain ?? job.description ?? "");
      return {
        externalKey: `lever:${target.slug}:${id}`,
        company: target.company,
        title: String(job.text ?? ""),
        url: String(job.hostedUrl ?? ""),
        location: String(cats.location ?? ""),
        description: desc.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(),
        source: "scan",
        vendor: target.vendor,
      } satisfies RawOpening;
    })
    .filter(Boolean) as RawOpening[];
}

export async function fetchAshby(target: ScanTarget): Promise<RawOpening[]> {
  const url = `https://api.ashbyhq.com/posting-api/job-board/${encodeURIComponent(target.slug)}?includeCompensation=true`;
  try {
    const payload = (await fetchJson(url)) as { jobs?: Record<string, unknown>[] };
    const jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    return jobs
      .map((job) => {
        const id = jobId(job.id);
        if (!id) return null;
        return {
          externalKey: `ashby:${target.slug}:${id}`,
          company: target.company,
          title: String(job.title ?? ""),
          url: String(job.jobUrl ?? ""),
          location: ashbyLocation(job),
          description: ashbyDescription(job),
          source: "scan",
          vendor: "ashby",
        } satisfies RawOpening;
      })
      .filter(Boolean) as RawOpening[];
  } catch (err) {
    if (!(err instanceof Error) || !err.message.includes("404")) throw err;
    return fetchAshbyGraphql(target);
  }
}

function ashbyLocation(job: Record<string, unknown>): string {
  const loc = String(job.location ?? "");
  return job.isRemote === true && !/remote|remoto/i.test(loc) ? `${loc} (Remote)`.trim() : loc;
}

/** Ashby keeps the pay band outside the description; put it at the top so salary parsing and scoring see it. */
function ashbyDescription(job: Record<string, unknown>): string {
  const comp = (job.compensation ?? {}) as { compensationTierSummary?: string; scrapeableCompensationSalarySummary?: string };
  const pay = String(comp.compensationTierSummary || comp.scrapeableCompensationSalarySummary || "").trim();
  const body = String(job.descriptionPlain ?? "").trim();
  return pay ? `Compensation: ${pay}\n\n${body}` : body;
}

/** Workable public widget API (apply.workable.com/<slug>). */
export async function fetchWorkable(target: ScanTarget): Promise<RawOpening[]> {
  const url = `https://apply.workable.com/api/v1/widget/accounts/${encodeURIComponent(target.slug)}?details=true`;
  const payload = (await fetchJson(url)) as { jobs?: Record<string, unknown>[] };
  const jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
  return jobs
    .map((job) => {
      const id = jobId(job.shortcode);
      if (!id) return null;
      const remote = String(job.telecommuting) === "true" || job.telecommuting === true;
      const place = [job.city, job.country].map((v) => String(v ?? "").trim()).filter(Boolean).join(", ");
      return {
        externalKey: `workable:${target.slug}:${id}`,
        company: target.company,
        title: String(job.title ?? ""),
        url: String(job.url ?? job.shortlink ?? ""),
        location: remote ? `${place}${place ? " " : ""}(Remote)` : place,
        description: String(job.description ?? "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(),
        source: "scan",
        vendor: "workable",
      } satisfies RawOpening;
    })
    .filter(Boolean) as RawOpening[];
}

/** Remote.com runs its own ATS at apply.remote.com with a public JSON API. */
function remoteComDocText(node: unknown): string {
  if (Array.isArray(node)) return node.map(remoteComDocText).join("");
  if (!node || typeof node !== "object") return "";
  const n = node as { text?: string; type?: string; content?: unknown };
  const inner = (n.text ?? "") + remoteComDocText(n.content);
  return ["heading", "paragraph", "listItem"].includes(String(n.type)) ? inner + "\n" : inner;
}

export function remoteComOpening(job: Record<string, unknown>, company = "Remote"): RawOpening | null {
  const id = jobId(job.public_id);
  if (!id) return null;
  const pay = (job.pay ?? {}) as { rows?: { range_text?: string }[] };
  const payText = (pay.rows ?? []).map((r) => String(r.range_text ?? "")).filter(Boolean).join("; ");
  const sections = Array.isArray(job.advert_sections) ? (job.advert_sections as { doc?: unknown }[]).map((x) => x.doc) : [];
  const desc = String(job.description ?? "").trim();
  const body = [desc === "-" ? "" : desc, remoteComDocText(sections)].filter(Boolean).join("\n\n").trim();
  const loc = String(job.location ?? "");
  return {
    externalKey: `remote-com:${id}`,
    company,
    title: String(job.title ?? ""),
    url: `https://apply.remote.com/jobs/${id}`,
    location: job.workplace === "remote" && !/remote/i.test(loc) ? `${loc} (Remote)`.trim() : loc,
    description: (payText ? `Compensation: ${payText}\n\n` : "") + body,
    source: "scan",
    vendor: "remote-com",
  };
}

const REMOTE_COM_HEADERS = {
  Accept: "application/json",
  "User-Agent": "Mozilla/5.0 (compatible; jobkit-scan/1)",
  Referer: "https://apply.remote.com/jobs",
};

export async function remoteComJson(url: string): Promise<unknown> {
  const res = await fetch(url, { headers: REMOTE_COM_HEADERS });
  if (!res.ok) throw new Error(`Remote.com ${res.status}`);
  return res.json();
}

export async function fetchRemoteCom(target: ScanTarget): Promise<RawOpening[]> {
  const payload = (await remoteComJson("https://apply.remote.com/api/public/jobs")) as { jobs?: Record<string, unknown>[] };
  return (payload.jobs ?? [])
    .filter((j) => j.status === "open")
    .map((j) => remoteComOpening(j, target.company))
    .filter(Boolean) as RawOpening[];
}

async function fetchAshbyGraphql(target: ScanTarget): Promise<RawOpening[]> {
  const query =
    "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { jobPostings { id title locationName } } }";
  const res = await fetch(
    "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams",
    {
      method: "POST",
      headers: { "User-Agent": UA, "Content-Type": "application/json" },
      body: JSON.stringify({
        operationName: "ApiJobBoardWithTeams",
        variables: { organizationHostedJobsPageName: target.slug },
        query,
      }),
    },
  );
  if (!res.ok) throw new Error(`Ashby GraphQL ${res.status}`);
  const payload = (await res.json()) as {
    data?: { jobBoard?: { jobPostings?: Record<string, unknown>[] } };
  };
  const jobs = payload.data?.jobBoard?.jobPostings ?? [];
  return jobs
    .map((job) => {
      const id = jobId(job.id);
      if (!id) return null;
      return {
        externalKey: `ashby:${target.slug}:${id}`,
        company: target.company,
        title: String(job.title ?? ""),
        url: `https://jobs.ashbyhq.com/${target.slug}/${id}`,
        location: String(job.locationName ?? ""),
        description: "",
        source: "scan",
        vendor: "ashby",
      } satisfies RawOpening;
    })
    .filter(Boolean) as RawOpening[];
}

export async function fetchRemotive(): Promise<RawOpening[]> {
  const payload = (await fetchJson("https://remotive.com/api/remote-jobs")) as {
    jobs?: Record<string, unknown>[];
  };
  const jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
  return jobs.map((job) => {
    const id = jobId(job.id);
    return {
      externalKey: `remotive:${id}`,
      company: String(job.company_name ?? ""),
      title: String(job.title ?? ""),
      url: String(job.url ?? ""),
      location: String(job.candidate_required_location ?? "Remote"),
      description: String(job.description ?? "").replace(/<[^>]+>/g, " ").trim(),
      source: "scan",
      vendor: "remotive",
    } satisfies RawOpening;
  });
}

export async function fetchArbeitnow(): Promise<RawOpening[]> {
  const payload = (await fetchJson("https://www.arbeitnow.com/api/job-board-api")) as {
    data?: Record<string, unknown>[];
  };
  const jobs = Array.isArray(payload.data) ? payload.data : [];
  return jobs.map((job) => {
    const slug = String(job.slug ?? job.id ?? "");
    return {
      externalKey: `arbeitnow:${slug}`,
      company: String(job.company_name ?? ""),
      title: String(job.title ?? ""),
      url: String(job.url ?? `https://www.arbeitnow.com/jobs/${slug}`),
      location: String(job.location ?? ""),
      description: String(job.description ?? "").replace(/<[^>]+>/g, " ").trim(),
      source: "scan",
      vendor: "arbeitnow",
    } satisfies RawOpening;
  });
}

export async function fetchAdzuna(env: Env, location = (env.ADZUNA_COUNTRY || "gb").toLowerCase()): Promise<RawOpening[]> {
  if (!env.ADZUNA_APP_ID || !env.ADZUNA_APP_KEY) return [];
  const url = new URL(
    `https://api.adzuna.com/v1/api/jobs/${location}/search/1`,
  );
  url.searchParams.set("app_id", env.ADZUNA_APP_ID);
  url.searchParams.set("app_key", env.ADZUNA_APP_KEY);
  url.searchParams.set("results_per_page", "50");
  url.searchParams.set("what", "engineer OR manager OR product");
  const payload = (await fetchJson(url.toString())) as {
    results?: Record<string, unknown>[];
  };
  const results = Array.isArray(payload.results) ? payload.results : [];
  return results.map((job) => {
    const id = jobId(job.id);
    return {
      externalKey: `adzuna:${location}:${id}`,
      company: String((job.company as { display_name?: string })?.display_name ?? ""),
      title: String(job.title ?? ""),
      url: String(job.redirect_url ?? ""),
      location: String((job.location as { display_name?: string })?.display_name ?? ""),
      description: String(job.description ?? "").replace(/<[^>]+>/g, " ").trim(),
      source: "scan",
      vendor: "adzuna",
    } satisfies RawOpening;
  });
}

export async function fetchAllSources(
  env: Env,
  targets: ScanTarget[],
): Promise<RawOpening[]> {
  const out: RawOpening[] = [];
  const tasks: Promise<RawOpening[]>[] = [];

  for (const t of targets) {
    if (t.vendor === "greenhouse") tasks.push(fetchGreenhouse(t));
    else if (t.vendor === "lever" || t.vendor === "lever-eu") tasks.push(fetchLever(t));
    else if (t.vendor === "ashby") tasks.push(fetchAshby(t));
    else if (t.vendor === "workable") tasks.push(fetchWorkable(t));
    else if (t.vendor === "remote-com") tasks.push(fetchRemoteCom(t));
    else if (t.vendor === "teamtailor") tasks.push(fetchTeamtailor(t));
  }
  tasks.push(fetchRemotive(), fetchArbeitnow(), fetchAdzuna(env));

  const batches = await Promise.allSettled(tasks);
  for (const b of batches) {
    if (b.status === "fulfilled") out.push(...b.value);
  }
  return out;
}
