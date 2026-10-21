# jobkit web: hosted job tracker

An optional, self-hosted companion to the jobkit plugin: a single-user web app on **Cloudflare Workers** with data in **your own Supabase project**. It finds jobs on the boards you watch, scores them against your own rubric with an LLM, tailors your CV per posting (Word and PDF export), drafts application answers, and tracks applications.

Everything personal (profile, rubric, watchlist, CV, AI key) lives in your database and is set up in the browser by a first-run wizard at **`/setup`**. Nothing personal is in this code.

## Features

- **Setup wizard** (`/setup`): connect Supabase, create the owner account, connect an AI model, describe what you are looking for, pick companies to watch, upload your CV and experience notes. Re-open it any time from **Settings**.
- **Jobs**: daily scan (Worker cron) of the boards you watch plus open feeds (Remotive, Arbeitnow, optional Adzuna). Prefilter on title, keywords and location; LLM scoring against your rubric (role tier, remote fit, focus, seniority, company type) with a deterministic remote-eligibility override; flags for things to check (right to work, non-competes, IT services). 👍 / 👎 / *Not interested* feedback refines the profile.
- **Job boards**: Ashby, Greenhouse, Lever (US and EU), Workable and Remote.com, rotated across runs to stay within the Workers free-plan subrequest limit. `POST /api/jobs/import` adds a posting by URL.
- **CVs**: a tailored CV per job, generated once from your Master CV and experience notes and saved; **Regenerate** with optional instructions. Profile paragraph, anti-echo checks (no copied posting phrases, no claiming titles you never held), preview, **Download PDF** (print) and **Download Word** from the built-in one-page template (steel-blue header, two columns).
- **Application answers**: *Prepare answers* reads the posting's application form (Ashby) or questions you paste, and drafts answers from your materials, marking the ones that need your input.
- **Experience notes**: upload .md, .txt, .pdf or .docx; text is extracted in the browser (files are not stored). Newer versions win when facts conflict.
- **Applications**: *I applied* on a job card logs the application with its CV and answers; undo, status, events, follow-ups.
- **Salary**: published ranges are parsed; otherwise an LLM estimate with sources is shown and cached.
- Glass design (light and dark), works on phones.

## Quick start (local)

Requirements: Node 22+ (wrangler 4.151 refuses older versions; `web/.nvmrc` pins 22), a free [Supabase](https://supabase.com) project, an API key for [OpenRouter](https://openrouter.ai/keys) (or OpenAI, or any OpenAI-compatible API).

```bash
cd web
npm install
cp .dev.vars.example .dev.vars   # optional: the wizard tells you what to put here
npm run dev                      # http://localhost:8787 → redirects to /setup
```

Then follow the wizard:

1. **Database**: paste the Project URL, publishable key and secret key (*Project Settings → API Keys*). The wizard tests them and prints the `.dev.vars` lines (including a freshly generated `SETTINGS_ENC_KEY`). Restart `npm run dev`, then run the setup SQL it offers (`/setup/jobkit-schema.sql`) in the Supabase SQL editor and click **Check again**.
2. **Account**: create your account. The first account becomes the owner; nobody else can open the app. Afterwards, turn off new sign-ups in Supabase (*Authentication → Sign In / Providers*).
3. **AI model**: choose OpenRouter (default), OpenAI or another OpenAI-compatible endpoint, paste the key, **Test & save**. The key is validated, encrypted with AES-GCM using `SETTINGS_ENC_KEY` and stored in `jobkit_settings`, readable only by the Worker.
4. **Preferences**: target roles by tier, seniority, focus, keywords, home location and remote rule, right to work, company types to exclude or flag, salary floor, anything else that matters, the scoring weights, companies to watch (names or board links; jobkit finds their public boards), and optionally your Master CV and experience notes.
5. **Finish**: opens the tracker.

## Deploy to Cloudflare

```bash
cd web
# 1. wrangler.jsonc → set "vars": SUPABASE_URL and SUPABASE_ANON_KEY (publishable key; safe to commit)
# 2. secrets (never in the repo)
npx wrangler secret put SUPABASE_SERVICE_ROLE_KEY
npx wrangler secret put SETTINGS_ENC_KEY      # 64 hex chars: openssl rand -hex 32
npx wrangler secret put SETUP_TOKEN           # optional, see below
# 3. deploy (also rebuilds public/setup/jobkit-schema.sql)
npm run deploy
```

Open the Worker URL and complete `/setup`. The cron in `wrangler.jsonc` (`0 7 * * *`, UTC) runs the daily scan for the owner. For a custom domain, add it under *Workers → your Worker → Settings → Domains & routes* and set `SITE_ORIGIN`.

**Private overrides.** Keep your own Worker name, custom domain, cron and vars out of the repo: copy `wrangler.local.example.jsonc` to `wrangler.local.jsonc` (gitignored). `npm run dev` and `npm run deploy` merge it over `wrangler.jsonc` (vars key by key; `triggers.crons` and `routes` replaced), so you can deploy an unmodified checkout. Pass `--name <worker>` to `wrangler secret put` when you rename the Worker.

**`SETUP_TOKEN`** (optional, recommended for public URLs): until an owner exists, anyone who reaches `/setup` could claim the instance. With `SETUP_TOKEN` set, claiming the owner account requires that token.

### Configuration

| Name | Kind | Purpose |
|------|------|---------|
| `SUPABASE_URL` | var | Project URL |
| `SUPABASE_ANON_KEY` | var | Publishable (anon) key; used by the browser, RLS protects rows |
| `SUPABASE_SERVICE_ROLE_KEY` | secret | Worker-only (scan, setup checks, settings); never sent to the browser |
| `SETTINGS_ENC_KEY` | secret | Encrypts the AI key saved in `/setup` (`enc:v1:` AES-256-GCM). Do not rotate without re-saving the key |
| `SETUP_TOKEN` | secret, optional | Required to claim the owner account |
| `OPENROUTER_API_KEY` | secret, optional | Fallback LLM key when none is saved in `/setup` |
| `JOBKIT_OWNER_USER_ID` | var, optional | Pin the owner instead of the account claimed in `/setup` |
| `SCAN_CRON_SECRET` | secret, optional | Enables `POST /api/scan/cron` with header `X-Scan-Cron-Secret` for external schedulers |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `ADZUNA_COUNTRY` | secret / var, optional | Adds Adzuna search results for that country (default `gb`) |
| `SITE_ORIGIN` | var, optional | Public URL, sent as `HTTP-Referer` to OpenRouter |
| `COOKIE_DOMAIN` | var, optional | Share the auth cookie across subdomains (e.g. `.example.com`) |
| `JOBKIT_LANG` | var, optional | UI language and language of AI-written reasons/flags/analysis: `en` (default) or `pt-PT` (European Portuguese). Locales live in `public/locales/` |
| `LOGO_DEV_TOKEN` | var, optional | [logo.dev](https://www.logo.dev) publishable token (`pk_…`) for company logos on jobs, applications and following. Without it each company shows its initial |
| `AUTH_MODE`, `LOGIN_URL` | var, optional | `AUTH_MODE=external` sends signed-out visitors to `LOGIN_URL?next=<url>` instead of `/login`, for when another app on the same Supabase project signs users in (set `COOKIE_DOMAIN` so the session cookie is shared) |
| `LOGOUT_URL`, `SETTINGS_URL`, `HOME_URL`, `HOME_LABEL` | var, optional | Where Sign out and Settings go, and an extra nav link (external mode hides Sign out without `LOGOUT_URL`) |
| `LLM_KEY_SOURCE` | var, optional | `table.column` holding a per-user OpenRouter key another app already stores (plain or `enc:v1:` with the same `SETTINGS_ENC_KEY`); used when no key is saved in `/setup` |

The Preferences tab also has **Scoring guidance** (appended to the scoring rubric) and **Writing rules** (applied to tailored CVs and application answers); `custom_flags` in the profile (`[{ "label": "…", "title": "<regex>" }]`) adds your own checks to matches.

### Database

`supabase/schema.sql` plus `supabase/migrations/*.sql` define the tables (all with Row Level Security; `jobkit_instance` and `jobkit_settings` have no policies, so only the service role can read them). `npm run build:sql` concatenates them into `public/setup/jobkit-schema.sql`, which is safe to re-run after upgrades. With the Supabase CLI you can instead `supabase link --project-ref <ref>` and `supabase db push`.

## How it works

- `src/index.ts`: routing. No Supabase config → `/setup`; no owner yet → `/setup`; not signed in as the owner → `/login`; profile not finished → `/setup`; otherwise the tracker.
- `src/setup.ts`: wizard API (`/api/setup/status`, `test-supabase`, `claim`, `llm`, `preferences`, `resolve-boards`, `finish`).
- `src/llm.ts`: provider settings, key encryption, OpenAI-compatible chat calls. The browser's AI features call `POST /api/ai/chat` on the Worker, so the key never reaches the browser.
- `src/scan/*`: sources, prefilter, scoring pipeline. `src/preferences.ts`: profile, rubric, feedback learning.
- `src/job-cv.ts`, `src/job-answers.ts`, `src/salary.ts`: per-job CV, answers, salary.
- `public/`: the tracker (`index.html`), wizard (`setup.html`, `setup.js`, `setup.css`), sign-in (`login.html`), CV renderer and Word template (`jobkit-cv-*.js`), vendored design system (`vendor/`).

## Development

```bash
npm test             # unit tests (prefilter, setup answers → profile, board links)
npm run typecheck
npm run screenshots  # demo-mode screenshots (no Supabase needed) into web/screenshots/
```

`index.html?demo=1` runs the tracker with demo data and no backend.
