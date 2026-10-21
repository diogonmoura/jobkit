/**
 * Company logos from the logo.dev CDN (publishable token in LOGO_DEV_TOKEN, injected as window.__JOBKIT_LOGO_TOKEN).
 * The domain is worked out from a careers or posting URL when it is the company's own site; otherwise the logo is
 * looked up by name. Without a token, or when no logo loads, an initial in a circle is shown instead.
 */
const ATS = /(^|\.)(ashbyhq\.com|greenhouse\.io|lever\.co|workable\.com|arbeitnow\.(com|nl|de)|smartrecruiters\.com|recruitee\.com|personio\.(de|com)|teamtailor\.com|myworkdayjobs\.com|workday\.com|bamboohr\.com|breezy\.hr|jobvite\.com|icims\.com|linkedin\.com|indeed\.[a-z.]+|glassdoor\.[a-z.]+|welcometothejungle\.com|otta\.com|wellfound\.com|ycombinator\.com|remoteok\.com|weworkremotely\.com|google\.com|notion\.site|example\.com)$/i;
const TWO_PART_TLD = /\.(co|com|org|net|gov|ac)\.[a-z]{2}$/i;

const norm = (s) => String(s || "").normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/[^a-z0-9]/g, "");

/** Registrable domain of a URL when it plausibly belongs to the company (not a job board / ATS host). */
export function companyDomain(url, company = "") {
  let host;
  try {
    host = new URL(String(url)).hostname.toLowerCase();
  } catch {
    return "";
  }
  if (!host || ATS.test(host)) return "";
  const parts = host.split(".");
  const base = parts.slice(TWO_PART_TLD.test(host) ? -3 : -2).join(".");
  const label = norm(base.split(".")[0]);
  const name = norm(company);
  if (name && label && !(name.includes(label) || label.includes(name) || label.startsWith(name.slice(0, 4)))) return "";
  return base;
}

/** Build a company → domain map from any rows that carry URLs (jobs, applications, following). */
export function domainMap(rows) {
  const map = new Map();
  for (const r of rows || []) {
    const key = norm(r && r.company);
    if (!key || map.has(key)) continue;
    for (const u of [r.domain && "https://" + r.domain, r.careers, r.posting, r.url, r.board_url]) {
      const d = u ? companyDomain(u, r.company) : "";
      if (d) {
        map.set(key, d);
        break;
      }
    }
  }
  return map;
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

export function logoUrl(company, domain, token, size = 64) {
  if (!token) return "";
  const id = domain ? encodeURIComponent(domain) : "name/" + encodeURIComponent(String(company || "").trim());
  return `https://img.logo.dev/${id}?token=${encodeURIComponent(token)}&size=${size}&retina=true&format=png&fallback=404`;
}

/** HTML for a small round logo with the initial underneath (shown until / unless the image loads). */
export function logoHtml(company, { domain = "", token = "", size = "" } = {}) {
  const initial = esc((String(company || "?").trim().match(/\p{L}|\p{N}/u) || ["?"])[0].toUpperCase());
  const src = logoUrl(company, domain, token);
  return `<span class="co-logo${size ? " co-logo--" + size : ""}" aria-hidden="true"><span class="co-logo__initial">${initial}</span>${
    src ? `<img src="${esc(src)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onload="this.parentNode.classList.add('has-img')" onerror="this.remove()" />` : ""
  }</span>`;
}
