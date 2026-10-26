# Choose where jobkit web runs: three install setups

Date: 2026-10-26
Status: draft, awaiting owner approval

## Intent

jobkit web only runs on Cloudflare Workers with the user's own Supabase project. Supabase's free plan allows two active projects, and when one of them gets stuck (as the owner's did in October 2026), jobkit has nowhere to go. A hosted tool that depends on one free tier is fragile, and "make a Supabase account" is also an extra hurdle for new users who already have a Vercel account.

This turns that problem into a feature: at install, the user picks one of three setups. The scanning, scoring, CV and tracker code stays one codebase; only the hosting and the database underneath change.

## Goals

Three supported, tested install setups:

| # | Hosting | Database | Login | How it's installed |
|---|---------|----------|-------|--------------------|
| 1 | Cloudflare Workers | Supabase | Supabase Auth | Today's route, unchanged |
| 2 | Vercel | Neon, added through Vercel Storage | Neon Auth | A "Deploy to Vercel" button in the README |
| 3 | Cloudflare Workers | Neon (connection string from Vercel Storage or neon.tech) | Neon Auth | wrangler deploy, as today, with a database URL instead of Supabase keys |

- Every feature in the README works the same on all three, except Supabase-only extras listed under Non-goals.
- On setups 2 and 3, login and password recovery come from Neon Auth (Neon's managed Better Auth, part of the Neon project), the way setup 1 relies on Supabase Auth. jobkit builds no password storage, sessions or reset flow of its own.
- The engine's `sync web` (and the daily GitHub Action in a user's data repo) works on all three.
- Existing setup-1 installs keep working with no action.

## Non-goals

- Moving data between setups. jobkit installs; a user who switches does it themselves. The README says how in two lines: install fresh, then `sync web` from `applications.md`.
- Any other hosting/database mix (Vercel + Supabase, other Postgres hosts). The code may happen to allow them; they are not offered or tested.
- Magic link and passkey login in the first release. Magic link comes later as its own PR; passkeys after that, if Neon Auth offers them.
- `AUTH_MODE=external` (sharing sign-in with another app on the same Supabase project) outside setup 1.
- Live updates between open tabs (the browser's Supabase realtime channel) outside setup 1.

## Anti-goals

- An existing setup-1 install that breaks or needs reconfiguring after upgrading.
- On setups 2 and 3, any way to read or write another user's rows, or any data without a signed-in owner.
- A database connection string or session secret reaching the browser or a committed file.
- Anyone other than the owner getting into the app, even though Neon Auth itself accepts sign-ups from anyone until sign-up is switched off.
- Two copies of the business logic (scan, scoring, CV, answers) that drift apart.
- New Python dependencies in the engine (it stays stdlib only, Python 3.9+).

## Decisions (with the owner)

1. Three setups, as in the table. Setup 3 is named "Cloudflare + Neon"; the docs offer two ways to get the database: Vercel Storage (no separate Neon account) or neon.tech.
2. Hosting is chosen at install, not in the wizard (the app is already running by the time `/setup` opens). The wizard adapts to the database it finds.
3. No migration tool between setups.
4. Login on Neon setups uses Neon Auth, chosen after a spike on 2026-10-26 (below). Email and password ships first; magic link, then passkeys, follow as separate PRs.
5. Database layer: keep the supabase-js-style call sites and add a Postgres translator (option A, see Database layer).
6. The owner's own move: setup 3 on his existing Worker and domain, with Neon through his Vercel account, then `sync web` from `applications.md`.

## Design

### Shape of the code

Two seams, chosen per install by configuration:

- **Host adapter**: Cloudflare (the current `export default { fetch, scheduled }` in `src/index.ts`) and Vercel (a catch-all function wrapping the same `fetch` handler). The app-level handler takes a small `Host` object for the three host-specific things the code uses today: static files (`env.ASSETS.fetch`, `src/index.ts:70`, `:110`, `:132`), background work (`ctx.waitUntil`) and the daily cron entry.
- **Database driver**: `JOBKIT_DB=supabase` (default, so existing installs need nothing) or `JOBKIT_DB=postgres` with `DATABASE_URL`.

### Database layer

The Worker has about 50 call sites using a small subset of supabase-js: `from(table)` with `select`, `insert`, `update`, `upsert` (with `onConflict`), `delete`, filters `eq`, `in`, `gte`, `not`, plus `order`, `limit`, `range`, `single`, `maybeSingle`. Owner's decision: keep those call sites, and add a Postgres driver that implements that same subset (option A). The alternative, rewriting every call site as named per-database functions (option B), was set aside: it touches all working setup-1 code at once for a cleanliness gain a single-user tracker doesn't need. Areas can still move to named functions later, one at a time, if the translator becomes a burden.

- `src/db/` exposes `serviceDb(env)` and `userDb(request, env)` returning a `Db` with `from(table)`. On `supabase` they return the real supabase-js clients (today's `createServiceSupabase` and `createSupabaseFromRequest`). On `postgres` they return a builder that compiles the chain to parameterized SQL and runs it with `@neondatabase/serverless` (works on both Workers and Vercel).
- The Postgres builder accepts only known tables and columns (an allowlist generated from the schema), never interpolates identifiers or values from requests, and returns `{ data, error }` in supabase-js's shape so callers don't change.
- `userDb` on `postgres` forces `user_id = <signed-in owner>` on every read and write, and sets it on every insert and upsert. This replaces what RLS does on Supabase. A query on a user-owned table without the forced filter must be impossible to express through `userDb`. `serviceDb` is for the scan, setup and engine endpoints, as today.
- Call sites that do something outside the subset get a small explicit function per case in `src/db/` instead of growing the builder.

### Schema

- Setup 1 keeps `supabase/schema.sql` and `supabase/migrations/` as they are.
- A Postgres variant, `postgres/schema.sql` plus `postgres/migrations/`, defines the same tables without Supabase specifics: user ids are Neon Auth's user ids instead of `auth.users` references, no `auth.uid()` defaults, no RLS policies (the Worker is the only client). Neon Auth keeps its own tables in the `neon_auth` schema; jobkit never writes to them. A test checks that both schemas define the same `jobkit_*` tables and columns, so they can't drift.
- On setups 2 and 3 the app applies the schema itself: the wizard's database step shows the database it found and a **Prepare database** button, which runs pending migrations recorded in a `jobkit_schema_migrations` table. No SQL to paste.

### Browser

- Setup 1: unchanged. The browser keeps talking to Supabase directly, and RLS protects rows.
- Setups 2 and 3: the browser can't reach the database. `public/jobkit-db.js` gets its client from a factory: on Postgres it's an object with the same `from(table)` builder that sends each query as JSON to `POST /api/db`, where the Worker runs it through `userDb`. `jobkit-db.js`'s own functions don't change.
- `/api/db` mirrors today's RLS exactly, as a per-table, per-operation browser allowlist (designer's call): the owner tables with today's owner policies get the operations those policies allow; `jobkit_salary_cache` is select only; `jobkit_scan_runs` is select and insert only; `jobkit_settings`, `jobkit_instance`, `jobkit_external_openings`, `jobkit_sync_state`, `jobkit_schema_migrations` and anything in the `neon_auth` schema are not reachable at all. The forced `user_id` filter applies to tables that have that column; tables without it are reachable only if listed here (today, `jobkit_salary_cache` for reads). A test pins the allowlist against the Supabase policies so a new table is closed until someone opens it on purpose.
- The realtime channel (`jobkit-db.js`, the one `.channel(` use) is skipped on Postgres; the page reloads its lists after its own writes and when the tab regains focus.
- `public/index.html`, `login.html` and `setup.js` stop importing `@supabase/ssr` from esm.sh on Postgres setups; the page config the Worker injects (`configScript`) says which mode is active.

### Login on Neon setups: Neon Auth

**What the spike proved** (2026-10-26, throwaway Neon database created through Vercel, throwaway Worker on Cloudflare, both deleted afterwards):

- A Neon database created from Vercel Storage gets Neon Auth from an **Auth** switch in Vercel's setup screen (on by default). Vercel shows `NEON_AUTH_BASE_URL` next to the database credentials.
- From a Cloudflare Worker: sign-up, sign-in, token verification and sign-out all worked. The Worker passed `/api/auth/*` through to `NEON_AUTH_BASE_URL` (stripping the cookie `Domain`, setting `Path=/`), so the session cookies were first-party and `HttpOnly`. It then fetched a JWT from Neon Auth's `/token` with the session cookie and verified it with `jose` against `<NEON_AUTH_BASE_URL>/.well-known/jwks.json` (EdDSA, Ed25519; issuer is the origin of `NEON_AUTH_BASE_URL`). After sign-out, `/token` returned 401.
- Neon Auth refuses requests from origins not on its **trusted domains** list (`403 INVALID_ORIGIN`). Each install's own address has to be added in the Neon console (*Settings → Better Auth → Domains*).
- Neon Auth settings include a **Sign-up with Email** switch, built-in **Magic Link**, Google sign-in with shared keys, and a shared email sender (`auth@mail.myneon.app`) for password reset. Neon recommends a custom email provider for production. Its Better Auth integration is labelled Beta.

**Design:**

- `/api/auth/*` is a pass-through to `NEON_AUTH_BASE_URL` (as in the spike), so the browser only ever talks to jobkit's own origin. `login.html` and `setup.js` call Better Auth's endpoints there (`sign-up/email`, `sign-in/email`, `sign-out`, and the password-reset endpoints) instead of supabase-js.
- `requireUser` (`src/auth.ts`) keeps its signature. On Postgres it gets a JWT for the request's session and verifies it locally against the JWKS (cached), then checks that `sub` is the owner. To avoid a Neon round trip on every request, the builder may keep the verified JWT in a short-lived `HttpOnly` cookie of jobkit's own and refresh it from `/token` when it expires (tokens last 15 minutes).
- **Owner only.** Until sign-up is switched off, anyone can create a Neon Auth account, so jobkit must never trust "signed in" alone: every request checks `sub` against the owner in `jobkit_instance`, as `requireUser` already does today.
- **Claiming the owner** (wizard account step): the user signs up through `/api/auth/sign-up/email`, then `POST /api/setup/owner` records the verified `sub` in `jobkit_instance`, guarded by `SETUP_TOKEN` when set and refused once an owner exists. The wizard then tells the user to switch off **Sign-up with Email** in the Neon console, with a link.
- **Recovery:** Neon Auth's password reset by email. `login.html` gets a "Forgot password?" link on Postgres setups.
- `jobkit_*.user_id` holds Neon Auth's user id (a uuid in the spike). The builder decides whether to add a foreign key to Neon Auth's `neon_auth` schema or keep `user_id` unconstrained.

### Vercel host (setup 2)

- `api/[[...path]].ts` (or the framework-free equivalent) wraps the shared `fetch` handler. `vercel.json` routes everything to it except static files in `public/`, which Vercel serves. The `Host.assets` implementation for Vercel reads the HTML templates the handler injects config into (`servePage`) from files bundled with the function.
- The daily scan is a Vercel Cron Job (`0 7 * * *`) calling the existing cron path; it authenticates with Vercel's `CRON_SECRET` header. Background work uses `waitUntil` from `@vercel/functions`.
- Configuration is Vercel environment variables. The Neon integration provides `DATABASE_URL` and, with Auth on, `NEON_AUTH_BASE_URL` (both seen in the spike).
- Neon says Vercel deployments made through the integration get trusted domains preconfigured; a custom domain still has to be added by hand. The builder confirms this on a real deploy and the README says what's left to do.
- The README gets a "Deploy to Vercel" button that clones the repo with `web/` as the root, asks for `SETTINGS_ENC_KEY` (and optional `SETUP_TOKEN`), and adds a Neon database if the button supports it; otherwise the README says to add it from the project's Storage tab, then redeploy.
- The scan's board rotation exists to stay under the Workers free-plan subrequest limit. On Vercel the limit that matters is function duration; the builder checks the Hobby plan's maximum and makes sure one scan run fits, adjusting the per-run batch on Vercel if needed.

### Engine bridge (`sync web`)

- New Worker endpoints under `/api/engine/` cover what `jobkit/jobkit_web.py`'s `Client` does today against Supabase's REST API: owner id, pull apps, insert app, conditional update and delete (by `updated_at`), and upsert external openings. The merge snapshot stays where it is today, in the file next to `applications.md` that `jobtrack_sync.sync_web` keeps and the Action commits; this work does not touch it. (`jobkit_sync_state` exists in the Supabase schema but is unused; the Postgres schema copies it only to keep the parity test simple.) They use `serviceDb`, so they work on all three setups. Auth: `Authorization: Bearer <JOBKIT_ENGINE_TOKEN>`, a secret set on the host.
- The engine gets a second client with the same methods, selected when `[tracker] web_url` is set; the token comes from `JOBKIT_ENGINE_TOKEN` in the environment or the env file named by `web_env` (default `~/.jobkit/web.env`), never `jobkit.toml`. The existing `supabase_url` client stays, so current configs keep working.
- The data repo's GitHub Action documentation adds the `web_url` variant (secret `JOBKIT_ENGINE_TOKEN`).
- `jobkit_apps.updated_at` must move on every update in the Postgres schema too (today a trigger does it on Supabase), since the conditional updates depend on it.

### Setup wizard

- The database step is driven by `JOBKIT_DB`. Supabase: as today. Postgres: shows the connection is working (host name only, never the URL) and the **Prepare database** button.
- On Postgres, the wizard first checks that Neon Auth is reachable and that this site's origin is trusted (a request from the Worker that returns `INVALID_ORIGIN` means it isn't), and if not, says exactly which address to add and where.
- The account step on Postgres signs up through `/api/auth/sign-up/email` instead of `sb.auth.signUp` (`setup.js:171`), claims the owner through `/api/setup/owner`, and signs in through `/api/auth/sign-in/email` instead of `signInWithPassword` (`setup.js:183`, `login.html:39`). Then it tells the user to switch off sign-up in Neon. The rest of the wizard is unchanged.

### Docs

- README: a short "Pick a setup" section with the table above, then one install section per setup. Configuration table gains `JOBKIT_DB`, `DATABASE_URL`, `NEON_AUTH_BASE_URL`, `JOBKIT_ENGINE_TOKEN`, and marks Supabase-only variables.
- Neon setups: turn on Auth when creating the database, add the site's address to Neon Auth's trusted domains, switch off sign-up after claiming the owner, and optionally set a custom email provider for reset emails.
- "Switching setups": install the new one, run `sync web`. jobkit does not move data.

### Delivery

Stacked PRs, in this order, so the owner can move to setup 3 after the second:

1. **Web: database layer, Postgres schema, Neon Auth login, `/api/db` and `/api/engine`.** Setups 1 and 3 work.
2. **Engine: `web_url` client** for `sync web`.
3. **Web: Vercel host and Deploy button.** Setup 2 works.
4. Later: magic link (Neon Auth has it built in, so mostly UI and docs). 5. Later: passkeys, if Neon Auth supports them by then.

### Tests

- The Postgres builder: unit tests that each supported chain compiles to the expected SQL and parameters, and that unknown tables or columns, and owner-less queries through `userDb`, are refused.
- Behavior tests that run the same calls through both drivers against a real Postgres. PGlite (in-process Postgres, dev dependency only) is the builder's suggested option; any approach that runs without a network and in the existing `npm test` is fine.
- Login: a fake Neon Auth (a local Ed25519 key pair serving JWKS and `/token`) to test token verification, expired and wrong-issuer tokens, a signed-in non-owner being refused, second-owner refusal, the `SETUP_TOKEN` guard, and the cookie rewriting in the `/api/auth/*` pass-through.
- Engine: the new client against a fake Worker, mirroring `test_jobkit_web.py`.
- Schema parity test between the Supabase and Postgres schemas.

## Left to the builder

- File and module names inside `src/db/` and `src/auth/`, and how the Vercel function entry is laid out.
- The exact JSON shape of `/api/db` requests.
- How migrations are tracked and run on Postgres, within "the app applies them from the wizard".
- Whether and how to cache the verified JWT between requests, within the constraints above.
