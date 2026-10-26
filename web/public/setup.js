// First-run setup wizard. Talks to /api/setup/* on this Worker; Supabase auth uses the publishable key only.
// On Postgres setups (JOBKIT_DB=postgres) accounts live in Neon Auth, reached through /api/auth/*.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
function toast(msg) { const t = $("toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), 3500); }
function setStatus(id, msg, kind = "") { const el = $(id); el.textContent = msg; el.className = "status-line" + (kind ? " " + kind : ""); }

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api/setup/" + path, { method, credentials: "include", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(json.error || `HTTP ${res.status}`); e.status = res.status; e.body = json; throw e; }
  return json;
}

const PG = window.__JOBKIT_DB?.mode === "postgres";
let status = null;
let client = null;
let boards = [];
let current = "db";
const ORDER = ["db", "account", "llm", "prefs", "done"];

function doneMap() {
  const s = status;
  const dbOk = PG
    ? Boolean(s?.supabase.configured && s.db && !s.db.error && s.db.pendingMigrations.length === 0 && s.db.neonAuth.reachable && s.db.neonAuth.originTrusted && s.encKey)
    : Boolean(s?.supabase.configured && s.supabase.checks?.schema.ok && s.encKey);
  return { db: dbOk, account: dbOk && s.isOwner, llm: s?.isOwner && s.llm.configured, prefs: s?.isOwner && s.prefsSaved, done: s?.onboarded };
}
function firstOpen() { const d = doneMap(); return ORDER.find((k) => !d[k]) ?? "done"; }
function canOpen(step) { const d = doneMap(); const i = ORDER.indexOf(step); return ORDER.slice(0, i).every((k) => d[k]); }

function go(step) {
  if (!canOpen(step)) { toast("Finish the earlier steps first"); return; }
  current = step;
  for (const p of document.querySelectorAll("[data-panel]")) p.hidden = p.dataset.panel !== step;
  const d = doneMap();
  for (const b of document.querySelectorAll("[data-step]")) {
    const on = b.dataset.step === step;
    b.classList.toggle("is-segment-active", on);
    b.classList.toggle("is-done", Boolean(d[b.dataset.step]) && !on);
    b.setAttribute("aria-current", on ? "step" : "false");
  }
  history.replaceState(null, "", "#" + step);
  if (step === "prefs") loadPrefs();
  if (step === "done") renderDone();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function checkLi(ok, label, detail) {
  return `<li class="${ok === null ? "wait" : ok ? "ok" : "bad"}"><span><b>${esc(label)}</b>${detail ? ` <span class="mf-muted">· ${esc(detail)}</span>` : ""}</span></li>`;
}
function renderChecks(c, extra = "") {
  $("db-checks").innerHTML = c
    ? checkLi(c.url.ok, "Project URL", c.url.detail) + checkLi(c.anonKey.ok, "Publishable key", c.anonKey.detail) + checkLi(c.serviceKey.ok, "Service role key", c.serviceKey.detail) + checkLi(c.schema.ok, "Tables", c.schema.detail) + extra
    : "";
}

function randomKey() { const b = new Uint8Array(32); crypto.getRandomValues(b); return [...b].map((x) => x.toString(16).padStart(2, "0")).join(""); }

function renderConfig(url, anon, needEncKey, needSupabase) {
  const enc = needEncKey ? randomKey() : null;
  const lines = [];
  if (needSupabase) lines.push(`SUPABASE_URL=${url}`, `SUPABASE_ANON_KEY=${anon}`, `SUPABASE_SERVICE_ROLE_KEY=<paste your secret key>`);
  if (enc) lines.push(`SETTINGS_ENC_KEY=${enc}`);
  $("cfg-devvars").textContent = lines.join("\n");
  const w = [];
  if (needSupabase) w.push(`// wrangler.jsonc → "vars"`, `"SUPABASE_URL": "${url}",`, `"SUPABASE_ANON_KEY": "${anon}"`, ``, `npx wrangler secret put SUPABASE_SERVICE_ROLE_KEY   # paste the secret key`);
  if (enc) w.push(`npx wrangler secret put SETTINGS_ENC_KEY   # paste: ${enc}`);
  $("cfg-wrangler").textContent = w.join("\n");
  $("db-config").hidden = false;
}

function renderDbPg() {
  const d = status.db;
  $("db-sb").hidden = true; $("db-pg").hidden = false;
  const na = d.neonAuth;
  $("pg-checks").innerHTML = !status.supabase.configured
    ? checkLi(false, "Configuration", "Set DATABASE_URL and NEON_AUTH_BASE_URL on the Worker, then click Check again")
    : checkLi(!d.error, "Database", d.error || d.host) +
      checkLi(!d.error && d.pendingMigrations.length === 0, "Tables", d.error ? "Unknown" : d.pendingMigrations.length ? `${d.pendingMigrations.length} migration(s) to apply` : "All jobkit tables are present") +
      checkLi(na.reachable && na.originTrusted, "Neon Auth", !na.reachable ? "Could not be reached: check NEON_AUTH_BASE_URL" : na.originTrusted ? "Reachable, and this site is trusted" : "This site is not trusted yet") +
      checkLi(status.encKey, "SETTINGS_ENC_KEY", status.encKey ? "Set (encrypts your AI key)" : "Missing: set a 64 character hex string as a Worker secret");
  const origin = $("pg-origin");
  origin.hidden = !(na.reachable && !na.originTrusted);
  origin.textContent = `Add ${na.origin} to Neon Auth's trusted domains: Neon console, Settings, Better Auth, Domains. Then click Check again.`;
  $("pg-prepare").disabled = !status.supabase.configured || !d.pendingMigrations.length;
  $("db-next").disabled = !doneMap().db;
}

function renderDb() {
  if (PG) { renderDbPg(); return; }
  const s = status.supabase;
  $("db-unconfigured").hidden = s.configured;
  $("db-config").hidden = true;
  $("db-schema").hidden = true;
  if (!s.configured) { renderChecks(null); $("db-next").disabled = true; return; }
  const c = s.checks;
  renderChecks(c, checkLi(status.encKey, "SETTINGS_ENC_KEY", status.encKey ? "Set (encrypts your AI key)" : "Missing: generate one below and add it as a Worker secret"));
  if (!status.encKey) renderConfig(s.url, "", true, false);
  if (c.serviceKey.ok && !c.schema.ok) {
    $("db-schema").hidden = false;
    if (s.projectRef) $("sql-editor-link").href = `https://supabase.com/dashboard/project/${encodeURIComponent(s.projectRef)}/sql/new`;
  }
  $("db-next").disabled = !doneMap().db;
}

async function ensureClient() {
  if (client) return client;
  if (PG) {
    const { createRemoteClient } = await import("/jobkit-remote-db.js");
    client = createRemoteClient();
    return client;
  }
  const cfg = window.__JOBKIT_SUPABASE;
  if (!cfg?.url || !cfg?.anonKey) return null;
  const { createBrowserClient } = await import("https://esm.sh/@supabase/ssr@0.6.1");
  client = createBrowserClient(cfg.url, cfg.anonKey, { cookieOptions: { path: "/", sameSite: "lax", secure: location.protocol === "https:" } });
  return client;
}

function renderAccount() {
  const owned = status.owner;
  $("acct-token-wrap").hidden = !status.setupTokenRequired;
  $("acct-pg-hint").hidden = !PG; $("acct-sb-hint").hidden = PG;
  if (status.isOwner) {
    $("acct-form").hidden = true; $("acct-done").hidden = false; $("acct-who").textContent = status.email;
    $("acct-title").textContent = "Your account"; $("acct-lede").textContent = "You are the owner of this jobkit.";
  } else {
    $("acct-form").hidden = false; $("acct-done").hidden = true;
    $("acct-create").hidden = owned;
    $("acct-signin").textContent = owned ? "Sign in" : "I already have one: sign in";
    $("acct-signin").className = owned ? "mf-btn mf-btn--primary" : "mf-btn mf-btn--ghost";
    $("acct-title").textContent = owned ? "Sign in" : "Create your account";
    $("acct-lede").textContent = owned ? "This jobkit already has an owner. Sign in with that account to change setup." : "jobkit is single-user. The first account created here becomes the owner; nobody else can open it.";
    $("acct-pass").autocomplete = owned ? "current-password" : "new-password";
  }
  $("acct-next").disabled = !status.isOwner;
}

function renderLlm() {
  const l = status.llm;
  if (l.configured) {
    const r = document.querySelector(`input[name=prov][value="${l.provider}"]`); if (r) r.checked = true;
    if (l.model) $("llm-model").value = l.model;
    if (l.baseUrl) $("llm-base").value = l.baseUrl;
    $("llm-key-hint").textContent = "A key is saved. Leave empty to keep it.";
  }
  syncProvider();
  $("llm-next").disabled = !l.configured;
}
function syncProvider() {
  const p = document.querySelector("input[name=prov]:checked").value;
  $("llm-base-wrap").hidden = p !== "custom";
  $("llm-model").placeholder = p === "openai" ? "gpt-4o-mini" : p === "anthropic" ? "claude-haiku-5-5 / claude-opus-5-5" : p === "custom" ? "model id" : "google/gemini-2.5-flash";
  $("llm-key").placeholder = p === "openrouter" ? "sk-or-…" : p === "openai" ? "sk-…" : p === "anthropic" ? "sk-ant-…" : "API key";
}

async function refresh() {
  status = await api("status");
  try { if (status.isOwner) { const p = await api("preferences"); status.prefsSaved = Boolean(p.profile?.title_tiers && Object.values(p.profile.title_tiers).some((a) => a.length)); status.prefsData = p; } } catch { /* ignore */ }
  $("open-app").hidden = !status.onboarded;
  renderDb(); renderAccount(); renderLlm();
}

// ---- step 1
$("sb-test").addEventListener("click", async () => {
  const url = $("sb-url").value.trim(), anon = $("sb-anon").value.trim(), svc = $("sb-service").value.trim();
  if (!url || !anon || !svc) { setStatus("sb-test-status", "Fill in all three fields", "err"); return; }
  setStatus("sb-test-status", "Testing…");
  try {
    const { checks } = await api("test-supabase", { method: "POST", body: { url, anonKey: anon, serviceKey: svc } });
    renderChecks(checks);
    const ok = checks.url.ok && checks.anonKey.ok && checks.serviceKey.ok;
    setStatus("sb-test-status", ok ? "Credentials work. Save them below." : "Fix the items marked ✕", ok ? "okc" : "err");
    if (ok) renderConfig(url.replace(/\/+$/, ""), anon, !status.encKey, true);
  } catch (e) { setStatus("sb-test-status", e.message, "err"); }
});
$("pg-prepare").addEventListener("click", async () => {
  setStatus("pg-status", "Preparing…");
  try {
    const r = await api("prepare-db", { method: "POST" });
    await refresh();
    setStatus("pg-status", r.applied.length ? `Applied ${r.applied.length} migration(s).` : "Already up to date.", "okc");
    go("db");
  } catch (e) { setStatus("pg-status", e.message, "err"); }
});
$("db-recheck").addEventListener("click", async () => { await refresh(); toast(doneMap().db ? "Database ready" : "Not ready yet"); go("db"); });
$("db-next").addEventListener("click", () => go("account"));
$("copy-sql").addEventListener("click", async () => { const t = await (await fetch("/setup/jobkit-schema.sql")).text(); await navigator.clipboard.writeText(t).then(() => toast("SQL copied"), () => toast("Copy failed: use the download link")); });
document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-copy]"); if (c) navigator.clipboard.writeText($(c.dataset.copy).textContent).then(() => toast("Copied"), () => toast("Copy failed"));
  const g = e.target.closest("[data-go]"); if (g) go(g.dataset.go);
  const s = e.target.closest("[data-step]"); if (s) go(s.dataset.step);
  const seg = e.target.closest(".seg button"); if (seg) { for (const b of seg.parentElement.children) b.classList.toggle("on", b === seg); }
});

// ---- step 2
// Postgres: Neon Auth through the Worker's /api/auth passthrough (first-party session cookie).
async function neonAuth(path, body) {
  const res = await fetch("/api/auth" + path, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify(body) });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(json.message || json.error || "Something went wrong. Try again."); e.status = res.status; e.code = json.code; throw e; }
  return json;
}
async function claim() {
  const token = $("acct-token").value.trim();
  await api("claim", { method: "POST", body: { token } });
}
$("acct-create").addEventListener("click", async () => {
  const email = $("acct-email").value.trim(), password = $("acct-pass").value;
  if (!email || password.length < 8) { setStatus("acct-status", "Enter an email and a password of 8+ characters", "err"); return; }
  setStatus("acct-status", "Creating…");
  try {
    if (PG) {
      // A retry after a failed claim finds the account already there: carry on with sign-in.
      let existed = false;
      try { await neonAuth("/sign-up/email", { name: email.split("@")[0], email, password }); }
      catch (e) { if (!(e.code === "USER_ALREADY_EXISTS" || e.status === 422)) throw e; existed = true; }
      try { await neonAuth("/sign-in/email", { email, password }); }
      catch (e) { throw existed ? new Error("This email already has an account and the password did not match. Use \"I already have one: sign in\" with the right password.") : e; }
      await claim(); await refresh(); setStatus("acct-status", ""); toast("Account created"); go("account");
      return;
    }
    const sb = await ensureClient();
    const { data, error } = await sb.auth.signUp({ email, password });
    if (error) throw error;
    if (!data.session) { setStatus("acct-status", "Check your inbox to confirm the address, then sign in here.", "okc"); return; }
    await claim(); await refresh(); setStatus("acct-status", ""); toast("Account created"); go("llm");
  } catch (e) { setStatus("acct-status", e.message || String(e), "err"); }
});
$("acct-signin").addEventListener("click", async () => {
  const email = $("acct-email").value.trim(), password = $("acct-pass").value;
  if (!email || !password) { setStatus("acct-status", "Enter your email and password", "err"); return; }
  setStatus("acct-status", "Signing in…");
  try {
    if (PG) {
      await neonAuth("/sign-in/email", { email, password });
      if (!status.owner) await claim();
      await refresh();
      if (!status.isOwner) { setStatus("acct-status", "This account is not the owner of this jobkit.", "err"); return; }
      setStatus("acct-status", ""); go("account");
      return;
    }
    const sb = await ensureClient();
    const { error } = await sb.auth.signInWithPassword({ email, password });
    if (error) throw error;
    if (!status.owner) await claim();
    await refresh();
    if (!status.isOwner) { setStatus("acct-status", "This account is not the owner of this jobkit.", "err"); return; }
    setStatus("acct-status", ""); go(firstOpen());
  } catch (e) { setStatus("acct-status", e.message || String(e), "err"); }
});
$("acct-next").addEventListener("click", () => go("llm"));

// ---- step 3
document.querySelectorAll("input[name=prov]").forEach((r) => r.addEventListener("change", syncProvider));
$("llm-save").addEventListener("click", async () => {
  const provider = document.querySelector("input[name=prov]:checked").value;
  setStatus("llm-status", "Testing the key…");
  try {
    const r = await api("llm", { method: "POST", body: { provider, baseUrl: $("llm-base").value.trim(), model: $("llm-model").value.trim(), apiKey: $("llm-key").value.trim() } });
    $("llm-key").value = "";
    await refresh();
    setStatus("llm-status", "✓ " + (r.test?.detail || "Key works") + ". Saved encrypted.", "okc");
    $("llm-next").disabled = false;
  } catch (e) { setStatus("llm-status", e.message, "err"); }
});
$("llm-next").addEventListener("click", () => go("prefs"));

// ---- step 4
const lines = (v) => String(v || "").split(/[\n,]+/).map((x) => x.trim()).filter(Boolean);
let prefsLoaded = false;
function loadPrefs() {
  if (prefsLoaded || !status.prefsData) return;
  prefsLoaded = true;
  const p = status.prefsData.profile || {};
  const t = p.title_tiers || {};
  const set = (id, v) => { if (v != null && v !== "") $(id).value = Array.isArray(v) ? v.join(id.startsWith("p-t") ? "\n" : ", ") : v; };
  set("p-t1", t.T1); set("p-t2", t.T2); set("p-t3", t.T3); set("p-t4", t.T4);
  set("p-seniority", p.seniority); set("p-industries", p.industries); set("p-focus", p.focus); set("p-keywords", p.keywords); set("p-kw-exclude", p.keywords_exclude);
  set("p-city", p.home_base?.city); set("p-country", p.home_base?.country); set("p-region", p.home_base?.region); set("p-auth", p.work_authorization);
  set("p-notes", p.notes); if (p.salary_floor?.amount) set("p-salary", p.salary_floor.amount); if (p.salary_floor?.currency) $("p-currency").value = p.salary_floor.currency;
  const home = [p.home_base?.city, p.home_base?.country].filter(Boolean);
  set("p-locations", (p.locations || []).filter((l) => !home.includes(l)));
  if (p.remote_policy) { const r = document.querySelector(`input[name=remote][value="${p.remote_policy}"]`); if (r) r.checked = true; }
  for (const seg of document.querySelectorAll("#p-avoid .seg")) {
    const label = seg.dataset.type.replace("_", " ");
    const v = (p.company_types_avoid || []).includes(label) ? "exclude" : (p.company_types_flag || []).includes(label) ? "flag" : (p.company_types_avoid || p.company_types_flag) && p.onboarded_at ? "" : null;
    if (v !== null) for (const b of seg.children) b.classList.toggle("on", b.dataset.v === v);
  }
  if (p.rubric) for (const r of document.querySelectorAll("#p-rubric [data-k]")) { r.value = p.rubric[r.dataset.k] ?? r.value; r.nextElementSibling.value = r.value; }
  if (p.min_score != null) { $("p-minscore").value = p.min_score; $("p-minscore").nextElementSibling.value = p.min_score; }
  boards = (status.prefsData.targets || []).map((x) => ({ company: x.company, vendor: x.vendor, slug: x.slug, jobs: null, checked: x.enabled }));
  renderBoards();
}
document.querySelectorAll("#p-rubric input").forEach((r) => r.addEventListener("input", () => (r.nextElementSibling.value = r.value)));

function renderBoards() {
  $("p-boards").innerHTML = boards.map((b, i) => `<li><input type="checkbox" id="bd-${i}" data-bd="${i}" ${b.checked !== false ? "checked" : ""}/><label for="bd-${i}"><b>${esc(b.company)}</b> <span class="vendor">${esc(b.vendor)}/${esc(b.slug)}${b.jobs != null ? ` · ${b.jobs} open roles` : ""}</span></label></li>`).join("");
}
$("p-boards").addEventListener("change", (e) => { const i = e.target.dataset.bd; if (i != null) boards[i].checked = e.target.checked; });
$("p-find").addEventListener("click", async () => {
  const companies = lines($("p-companies").value).filter((c) => !boards.some((b) => b.company.toLowerCase() === c.toLowerCase()));
  if (!companies.length) { setStatus("p-find-status", "Add company names or board links first", "err"); return; }
  setStatus("p-find-status", `Looking up ${Math.min(companies.length, 12)} compan${companies.length === 1 ? "y" : "ies"}…`);
  try {
    const r = await api("resolve-boards", { method: "POST", body: { companies } });
    for (const f of r.found) if (!boards.some((b) => b.vendor === f.vendor && b.slug === f.slug)) boards.push({ ...f, checked: true });
    renderBoards();
    setStatus("p-find-status", `Found ${r.found.length}.` + (r.notFound.length ? ` Not found: ${r.notFound.join(", ")} (paste their careers-page link if they use a supported ATS).` : ""), r.found.length ? "okc" : "err");
  } catch (e) { setStatus("p-find-status", e.message, "err"); }
});

async function readFiles(input) {
  const { extractText, fileKind } = await import("/jobkit-extract.js");
  const out = [];
  for (const f of input.files) {
    if (!fileKind(f)) { toast("Skipped (unsupported format): " + f.name); continue; }
    const { kind, text } = await extractText(f);
    if (text.trim()) out.push({ filename: f.name, content: text, size: f.size, kind });
  }
  return out;
}

$("p-save").addEventListener("click", async () => {
  const seg = {}; for (const s of document.querySelectorAll("#p-avoid .seg")) seg[s.dataset.type] = s.querySelector(".on")?.dataset.v ?? "";
  const rubric = {}; for (const r of document.querySelectorAll("#p-rubric [data-k]")) rubric[r.dataset.k] = Number(r.value);
  const answers = {
    tiers: { T1: $("p-t1").value, T2: $("p-t2").value, T3: $("p-t3").value, T4: $("p-t4").value },
    seniority: $("p-seniority").value, industries: $("p-industries").value, focus: $("p-focus").value,
    keywords: $("p-keywords").value, keywords_exclude: $("p-kw-exclude").value,
    home_city: $("p-city").value, home_country: $("p-country").value, home_region: $("p-region").value, locations: $("p-locations").value,
    remote_policy: document.querySelector("input[name=remote]:checked").value, work_authorization: $("p-auth").value,
    avoid: seg, exclude_companies: $("p-exclude-co").value,
    salary_amount: Number($("p-salary").value) || 0, salary_currency: $("p-currency").value,
    characteristics: $("p-notes").value, rubric, min_score: Number($("p-minscore").value),
  };
  if (!lines(answers.tiers.T1).length && !lines(answers.tiers.T2).length) { setStatus("p-status", "Add at least one Tier 1 or Tier 2 role", "err"); $("p-t1").focus(); return; }
  setStatus("p-status", "Saving…");
  try {
    await api("preferences", { method: "POST", body: { answers, boards: boards.filter((b) => b.checked !== false) } });
    const sb = await ensureClient();
    // On Postgres the Worker sets user_id from the session, so the browser does not need it.
    const user = PG ? { id: undefined } : (await sb.auth.getUser()).data.user;
    if ($("p-cv").files.length) {
      const [cv] = await readFiles($("p-cv"));
      if (cv) { const { error } = await sb.from("jobkit_profile").upsert({ user_id: user.id, master_cv: cv.content, updated_at: new Date().toISOString() }, { onConflict: "user_id" }); if (error) throw error; status.cvSaved = cv.filename; }
    }
    if ($("p-exp").files.length) {
      const files = await readFiles($("p-exp"));
      for (const f of files) { const { error } = await sb.from("jobkit_experience_files").insert({ user_id: user.id, filename: f.filename, content: f.content, size: f.size, kind: f.kind, enabled: true, version_date: new Date().toISOString().slice(0, 10) }); if (error) throw error; }
      status.expSaved = files.length;
    }
    $("p-cv").value = ""; $("p-exp").value = "";
    setStatus("p-status", "");
    const keepCv = status.cvSaved, keepExp = status.expSaved;
    prefsLoaded = false; await refresh(); status.cvSaved = keepCv; status.expSaved = keepExp;
    go("done");
  } catch (e) { setStatus("p-status", e.message || String(e), "err"); }
});

// ---- step 5
function renderDone() {
  const p = status.prefsData?.profile || {};
  const t = p.title_tiers || {};
  const n = (status.prefsData?.targets || []).filter((x) => x.enabled).length;
  $("done-summary").textContent = `jobkit will look for ${[...(t.T1 || []), ...(t.T2 || [])].slice(0, 3).join(", ") || "your target roles"}${p.remote_policy === "remote_only" ? ", remote only" : ""}.`;
  $("done-checks").innerHTML =
    checkLi(true, "Database", PG ? status.db.host : status.supabase.url.replace(/^https?:\/\//, "")) +
    checkLi(true, "Owner", status.email) +
    checkLi(true, "AI model", `${status.llm.provider}${status.llm.model ? " · " + status.llm.model : ""}`) +
    checkLi(true, "Preferences", `${(p.roles || []).length} roles · min score ${p.min_score ?? 50}`) +
    checkLi(n > 0, "Companies to watch", n ? `${n} job board${n === 1 ? "" : "s"}` : "None yet: the open feeds (Remotive, Arbeitnow) are still scanned") +
    (status.cvSaved ? checkLi(true, "Master CV", status.cvSaved) : "") +
    (status.expSaved ? checkLi(true, "Experience notes", `${status.expSaved} file(s)`) : "");
}
$("finish").addEventListener("click", async () => {
  try { await api("finish", { method: "POST" }); location.href = "/"; } catch (e) { toast(e.message); }
});

// ---- boot
(async () => {
  try { await refresh(); } catch (e) { toast("Could not load setup status: " + e.message); return; }
  // Supabase only: on Postgres the remote client is loaded after sign-in, where it is needed.
  if (!PG && status.supabase.configured && status.supabase.checks?.schema.ok) { try { await ensureClient(); } catch { /* optional here; used again when needed */ } }
  const want = location.hash.slice(1);
  go(ORDER.includes(want) && canOpen(want) ? want : firstOpen());
})();
