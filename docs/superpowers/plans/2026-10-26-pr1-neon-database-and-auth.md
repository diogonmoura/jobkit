# PR 1: Neon database layer and Neon Auth login, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** jobkit web runs on Cloudflare Workers with either Supabase (setup 1, unchanged) or Neon plus Neon Auth (setup 3), chosen by `JOBKIT_DB`.

**Architecture:** Every database call site keeps its supabase-js-style chain (`db.from(t).select().eq()`), typed as a small `Db` interface. On `JOBKIT_DB=supabase` that interface is the real supabase-js client; on `postgres` it is a translator that compiles the chain to parameterized SQL and runs it on Neon over HTTP. On Postgres, the browser reaches the database only through `POST /api/db` (an allowlist that mirrors today's RLS), and login goes through a pass-through to Neon Auth with tokens verified in the Worker.

**Tech Stack:** TypeScript on Cloudflare Workers, `@neondatabase/serverless` (HTTP driver), `jose` (JWT/JWKS), `@electric-sql/pglite` (dev only, in-process Postgres for tests), `node:test` via `tsx --test`.

**Spec:** `docs/superpowers/specs/2026-10-26-backend-choice-design.md` (approved 2026-10-26). This plan covers its Delivery item 1. Items 2 (engine `web_url` client) and 3 (Vercel host) get their own plans.

## Global Constraints

- All work is in `web/`. Run commands from `web/`. Tests: `npm test`; types: `npm run typecheck`. Both must pass at every commit.
- `JOBKIT_DB` unset or `supabase` must behave exactly as today. Existing setup-1 installs need no new config.
- New env (all optional on setup 1): `JOBKIT_DB` (`supabase` | `postgres`), `DATABASE_URL`, `NEON_AUTH_BASE_URL`, `JOBKIT_ENGINE_TOKEN`.
- Never send `DATABASE_URL`, `JOBKIT_ENGINE_TOKEN`, `SUPABASE_SERVICE_ROLE_KEY` or `SETTINGS_ENC_KEY` to the browser or include them in an API response. The wizard shows the database host name only.
- The Postgres translator never interpolates a table, column or value from input into SQL text: identifiers come from the allowlist, values go in `$n` parameters.
- On Postgres, every user-facing read and write is limited to the owner: `sub` of a verified Neon Auth token must equal `jobkit_instance.owner_user_id`. A signed-in non-owner gets 401 everywhere.
- JWT checks: algorithm `EdDSA`, JWKS at `<NEON_AUTH_BASE_URL>/.well-known/jwks.json`, issuer = `new URL(NEON_AUTH_BASE_URL).origin`.
- Commit messages: plain imperative subject, no prefix, ending with the `Co-Authored-By` line the session gives. Commit on branch `backend-choice-design` (stacked PRs come from it later).
- Text in UI and docs: no em dashes, plain voice (the owner's standing rule).
- Testing departure from the spec: the spec asks for the same calls run through both drivers against a real Postgres. supabase-js needs Supabase's REST layer, which can't run offline, so the Supabase side is pinned by the result shapes listed in Task 3 (taken from supabase-js's documented behavior) and by Task 12 step 7, and only the Postgres driver runs on PGlite.
- `LLM_KEY_SOURCE` (reading a key from another app's table) stays Supabase-only: on Postgres that table isn't in the allowlist. The README marks it so.

## Review Focus

1. **A signed-in stranger.** Neon Auth accepts sign-ups from anyone until the owner switches sign-up off. Such a user must get 401 from `/api/*`, `/api/db` and be sent to `/login` for pages. Pinned in Tasks 6 and 8.
2. **Claiming the owner twice, or without `SETUP_TOKEN`.** A second `POST /api/setup/owner` after an owner exists must be refused (409), and with `SETUP_TOKEN` set a wrong token must be refused (403). Pinned in Task 10.
3. **An untrusted origin on Neon Auth.** If the site's address isn't on Neon Auth's trusted domains, the wizard must say which address to add, not show a raw 403. Pinned in Task 10.
4. **The conditional application update.** `jobkit-db.js` updates `jobkit_apps` with `.eq("updated_at", x).select("id")` and treats an empty result as "changed elsewhere". The translator must return `[]` (not `null`, not an error) when no row matched, and `updated_at` must move on every update. Pinned in Tasks 2 and 3.
5. **`head: true` counts.** `scan/pipeline.ts:228` and `:564` read `{ count }` with `head: true`; the translator must return `count` as a number and `data: null`. Pinned in Tasks 1 and 3.

---

### Task 1: The `Db` interface and the SQL compiler

**Files:**
- Create: `web/src/db/types.ts`, `web/src/db/compile.ts`
- Test: `web/src/db/compile.test.ts`

**Interfaces:**
- Produces, in `types.ts`:
  ```ts
  export type DbError = { message: string; code?: string };
  export type DbResult<T = any> = { data: T | null; error: DbError | null; count?: number | null };
  export interface DbQuery<T = any> extends PromiseLike<DbResult<T>> {
    select(columns?: string, opts?: { count?: "exact"; head?: boolean }): DbQuery<T>;
    insert(rows: Record<string, unknown> | Record<string, unknown>[]): DbQuery<T>;
    update(patch: Record<string, unknown>): DbQuery<T>;
    upsert(rows: Record<string, unknown> | Record<string, unknown>[], opts?: { onConflict?: string }): DbQuery<T>;
    delete(): DbQuery<T>;
    eq(column: string, value: unknown): DbQuery<T>;
    in(column: string, values: unknown[]): DbQuery<T>;
    gte(column: string, value: unknown): DbQuery<T>;
    order(column: string, opts?: { ascending?: boolean; nullsFirst?: boolean }): DbQuery<T>;
    limit(n: number): DbQuery<T>;
    single(): DbQuery<T>;
    maybeSingle(): DbQuery<T>;
  }
  export interface Db { from(table: string): DbQuery }
  export type QueryPlan = { table: string; op: "select" | "insert" | "update" | "upsert" | "delete"; columns?: string; count?: "exact"; head?: boolean;
    rows?: Record<string, unknown>[]; patch?: Record<string, unknown>; onConflict?: string; returning?: string;
    filters: { column: string; op: "eq" | "in" | "gte"; value: unknown }[];
    order: { column: string; ascending: boolean; nullsFirst?: boolean }[]; limit?: number; cardinality?: "single" | "maybeSingle" };
  export type TableSpec = { columns: string[]; jsonColumns: string[]; owned: boolean };
  ```
  These are exactly the operations the code uses today (inventory in the spec's Database layer section). Nothing else is supported.
- Produces, in `compile.ts`: `compile(plan: QueryPlan, tables: Record<string, TableSpec>): { text: string; values: unknown[] }`. Throws `DbCompileError` (exported) for an unknown table or column.

- [ ] **Step 1: Write the failing tests** in `compile.test.ts`, with a two-table fixture spec (`jobkit_apps` with `user_id, id, company, status, payload, updated_at`, `payload` as JSON; `jobkit_salary_cache` with `cache_key, payload`). Cases and exact expected output:
  - `select` with `columns: "id, company"`, filter `eq user_id "u1"`, order `updated_at` desc, `limit 5` → `select "id", "company" from "jobkit_apps" where "user_id" = $1 order by "updated_at" desc limit 5`, values `["u1"]`.
  - `select` with no columns → `select *`.
  - `select` with `count: "exact", head: true` → `select count(*)::int as count from "jobkit_apps" where …`.
  - `in` with `["a","b"]` → `"id" = any($1)` with values `[["a","b"]]`.
  - order with `nullsFirst: true, ascending: true` → `order by "x" asc nulls first`.
  - `insert` two rows with different key sets → one statement over the union of keys, missing keys as `default`.
  - `upsert` with `onConflict: "user_id,id"` → `insert … on conflict ("user_id", "id") do update set <every non-conflict column> = excluded.<col>`.
  - `update` with patch and filters and `returning: "id"` → `update … set … where … returning "id"`.
  - JSON column values are passed as `JSON.stringify(value)` with a `::jsonb` cast.
  - unknown table, unknown column in `columns`, filter, order, patch or row keys → throws `DbCompileError`.
- [ ] **Step 2:** Run `npx tsx --test src/db/compile.test.ts`. Expected: FAIL (module not found).
- [ ] **Step 3:** Implement `types.ts` and `compile.ts`. Quote every identifier with double quotes after checking it against `tables`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Add `src/db/*.test.ts` to the `test` script glob in `package.json`, run `npm test`, then commit: `Add the Db interface and the Postgres query compiler`.

### Task 2: Postgres schema, migrations and the table allowlist

**Files:**
- Create: `web/postgres/schema.sql`, `web/postgres/migrations/` (start empty: `schema.sql` is the baseline), `web/scripts/build-postgres-sql.mjs`, `web/src/db/tables.ts`, `web/src/db/postgres-sql.generated.ts` (generated, committed)
- Modify: `web/package.json` (`build:sql` also runs the new script; devDependency `@electric-sql/pglite`)
- Test: `web/src/db/schema.test.ts`

**Interfaces:**
- Produces: `TABLES: Record<string, TableSpec>` in `tables.ts` (all `jobkit_*` tables and their columns; `owned` = has `user_id`; `jsonColumns` = `json`/`jsonb` columns). `POSTGRES_MIGRATIONS: { id: string; sql: string }[]` in the generated file, `id` = file name, baseline first with id `0000_schema.sql`.

- [ ] **Step 1: Write the failing tests** in `schema.test.ts`:
  - `schema parity`: parse `create table` and `add column` statements from `supabase/schema.sql` plus `supabase/migrations/*.sql`, and from `postgres/schema.sql` plus `postgres/migrations/*.sql`; assert the same `jobkit_*` tables with the same column names.
  - `allowlist matches schema`: `TABLES` has exactly those tables and columns.
  - `schema applies`: load PGlite, run every `POSTGRES_MIGRATIONS` entry in order, twice; no error the second time.
  - `updated_at moves`: insert into `jobkit_apps`, update it, assert `updated_at` increased.
- [ ] **Step 2:** Run `npx tsx --test src/db/schema.test.ts`. Expected: FAIL.
- [ ] **Step 3:** Write `postgres/schema.sql`: the current end state of every `jobkit_*` table from the Supabase files, re-runnable (`if not exists`), with these differences only: `user_id uuid not null` with no `auth.uid()` default and no reference to `auth.users`; no RLS, no policies, no realtime publication; keep the `jobkit_apps_touch` trigger; add `create table if not exists jobkit_schema_migrations (id text primary key, applied_at timestamptz not null default now())`. Write `build-postgres-sql.mjs` to emit `postgres-sql.generated.ts`, then hand-write `tables.ts` (the test keeps it honest).
- [ ] **Step 4:** Run the test. Expected: PASS.
- [ ] **Step 5:** `npm test` and `npm run typecheck`, then commit: `Add the Postgres schema and table allowlist`.

### Task 3: The Postgres driver

**Files:**
- Create: `web/src/db/postgres.ts`
- Modify: `web/package.json` (dependency `@neondatabase/serverless`)
- Test: `web/src/db/postgres.test.ts`

**Interfaces:**
- Consumes: `compile`, `TABLES`, `POSTGRES_MIGRATIONS`, the types from Tasks 1 and 2.
- Produces:
  ```ts
  export type SqlRunner = (text: string, values: unknown[]) => Promise<Record<string, unknown>[]>;
  export function neonRunner(databaseUrl: string): SqlRunner;           // @neondatabase/serverless neon(url).query(text, values)
  export function createPostgresDb(run: SqlRunner, scope: { ownerId?: string; access?: BrowserAccess }): Db;
  export async function applyMigrations(run: SqlRunner): Promise<string[]>;   // returns ids applied now
  export async function pendingMigrations(run: SqlRunner): Promise<string[]>;
  ```
  (`BrowserAccess` comes from Task 7; until then `access` is unused.)
- Result shapes, matching supabase-js: `select` returns `data` as an array; `single()` returns one object or `error` with code `PGRST116` when not exactly one row; `maybeSingle()` returns the object or `null`; mutations without `.select()` return `data: null`; mutations with `.select(cols)` return the returned rows (an empty array when none matched); `head: true` returns `data: null, count: n`. SQL errors come back as `error: { message, code }`, never thrown.
- With `scope.ownerId` set: on tables with `owned: true`, add `user_id = ownerId` to every filter and set `user_id = ownerId` on every inserted or upserted row (overwriting any value given).

- [ ] **Step 1: Write the failing tests** in `postgres.test.ts`, running on PGlite after `applyMigrations` (a `SqlRunner` wrapping `pglite.query`):
  - insert then select back, with a JSON column round trip.
  - upsert twice on `user_id,id` updates in place.
  - conditional update `.update(p).eq("id", x).eq("updated_at", stale).select("id")` → `{ data: [], error: null }`; with the current `updated_at` → `data` has one row.
  - `select("job_id", { count: "exact", head: true })` → `{ data: null, count: 2 }`.
  - `single()` on zero rows → `error.code === "PGRST116"`; `maybeSingle()` → `data: null, error: null`.
  - owner scope: rows of user `u2` are invisible to a db scoped to `u1`; an insert with `user_id: "u2"` through the `u1` db is stored as `u1`.
  - `pendingMigrations` lists the baseline before `applyMigrations` and nothing after.
- [ ] **Step 2:** Run `npx tsx --test src/db/postgres.test.ts`. Expected: FAIL.
- [ ] **Step 3:** Implement `postgres.ts`: a chainable builder that records a `QueryPlan` and runs it on `then`.
- [ ] **Step 4:** Run the test. Expected: PASS.
- [ ] **Step 5:** `npm test`, `npm run typecheck`, commit: `Add the Postgres driver for the Db interface`.

### Task 4: Route every server call site through `Db`

**Files:**
- Create: `web/src/db/index.ts`
- Modify: `web/src/types.ts` (add `JOBKIT_DB`, `DATABASE_URL`, `NEON_AUTH_BASE_URL`, `JOBKIT_ENGINE_TOKEN` to `Env`), `web/src/auth.ts`, and every file that takes a `SupabaseClient`: `salary.ts`, `setup.ts`, `watchlist.ts`, `scan/pipeline.ts`, `scan/suitability.ts`, `scan/engine.ts`, `preferences.ts`, `experience.ts`, `job-answers.ts`, `job-cv.ts`, `api.ts`, `index.ts`
- Test: `web/src/db/index.test.ts`

**Interfaces:**
- Produces, in `db/index.ts`:
  ```ts
  export type DbMode = "supabase" | "postgres";
  export function dbMode(env: Env): DbMode;              // "postgres" only when JOBKIT_DB === "postgres"
  export function dbConfigured(env: Env): boolean;       // supabase: today's supabaseConfigured; postgres: DATABASE_URL && NEON_AUTH_BASE_URL
  export function serviceDb(env: Env): Db;               // supabase: createServiceSupabase(env) as unknown as Db; postgres: createPostgresDb(neonRunner(env.DATABASE_URL), {})
  export function ownerDb(env: Env, ownerId: string): Db; // supabase: same as the user's supabase client today; postgres: createPostgresDb(..., { ownerId })
  ```
- Every parameter typed `SupabaseClient` becomes `Db`. `createServiceSupabase(env)` calls outside `db/index.ts` and `auth.ts` become `serviceDb(env)`. `supabaseConfigured(env)` checks in `index.ts`, `setup.ts` and `getOwnerId` become `dbConfigured(env)`.

- [ ] **Step 1: Write the failing test** `index.test.ts`: `dbMode({})` is `"supabase"`; `dbMode({ JOBKIT_DB: "postgres" })` is `"postgres"`; `dbConfigured` true/false for each mode with and without its variables.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3:** Implement `db/index.ts`, then retype the call sites. This step changes types and the source of the client only, no query.
- [ ] **Step 4:** `npm test` and `npm run typecheck`. Expected: PASS, with no change to any existing test.
- [ ] **Step 5:** Commit: `Type every database call site as Db`.

### Task 5: Neon Auth token verification

**Files:**
- Create: `web/src/auth/neon.ts`
- Modify: `web/package.json` (dependency `jose`)
- Test: `web/src/auth/neon.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export async function verifyNeonToken(token: string, env: Env, fetchImpl?: typeof fetch): Promise<{ userId: string; email?: string } | null>;
  export async function neonSessionUser(request: Request, env: Env, fetchImpl?: typeof fetch): Promise<{ userId: string; email?: string } | null>;
  ```
  `neonSessionUser` calls `GET <NEON_AUTH_BASE_URL>/token` with the request's `Cookie` header and `Origin` set to the request's origin, reads `{ token }`, then `verifyNeonToken`. JWKS is fetched with `createRemoteJWKSet` and kept in a module-level cache per base URL. Returns `null` on any failure, never throws.

- [ ] **Step 1: Write the failing tests**, with a local Ed25519 key pair (`jose.generateKeyPair("EdDSA")`) and a fake `fetchImpl` serving the JWKS and `/token`:
  - valid token → `{ userId: sub }`.
  - expired token, wrong issuer, `HS256` token, token signed by another key → `null`.
  - `/token` answering 401 → `null`.
- [ ] **Step 2:** Run `npx tsx --test src/auth/neon.test.ts`. Expected: FAIL. Add `src/auth/*.test.ts` to the test glob.
- [ ] **Step 3:** Implement. Pass `fetchImpl` through to `createRemoteJWKSet` via its `[customFetch]` option so tests stay offline.
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit: `Verify Neon Auth sessions in the Worker`.

### Task 6: The `/api/auth/*` pass-through and `requireUser` on Postgres

**Files:**
- Create: `web/src/auth/proxy.ts`
- Modify: `web/src/auth.ts` (`requireUser`, `AuthContext`), `web/src/api.ts` (route `/auth/*` first; use `auth.db`), `web/src/index.ts` (page gate, `configScript`)
- Test: `web/src/auth/proxy.test.ts`, `web/src/auth/require-user.test.ts`

**Interfaces:**
- Produces: `proxyNeonAuth(request: Request, env: Env, path: string, fetchImpl?: typeof fetch): Promise<Response>` in `proxy.ts`. Forwards method, body, query and headers (minus `host`) to `<NEON_AUTH_BASE_URL><path>`; on the way back drops `Domain` from every `Set-Cookie` and sets `Path=/`; `redirect: "manual"`.
- `AuthContext` becomes `{ userId: string; db: Db; pending: CookiePending[] }`. `api.ts` reads `db` where it read `supabase`. On Supabase, `db` is today's request client; on Postgres it is `ownerDb(env, userId)`.
- `requireUser` on Postgres: `neonSessionUser`, then `userId === await getOwnerId(env)` or `null`.
- `index.ts` page gate on Postgres uses the same check instead of `supabase.auth.getUser()`. `configScript` adds `window.__JOBKIT_DB = { mode }` and, on Postgres, sets `__JOBKIT_SUPABASE` to `null`.
- `/api/auth/*` is only routed when `dbMode(env) === "postgres"`; on Supabase those paths stay 404 as today.

- [ ] **Step 1: Write the failing tests:**
  - `proxy.test.ts`: a `Set-Cookie` of `__Secure-x=1; Domain=neon.tech; Path=/neondb/auth; HttpOnly; Secure` comes back as `__Secure-x=1; Path=/; HttpOnly; Secure`; two cookies stay two headers; status and body pass through; the upstream URL keeps the query string.
  - `require-user.test.ts` (Postgres env, stubbed `neonSessionUser` and owner): owner → context with `userId`; signed-in non-owner → `null`; no session → `null`.
- [ ] **Step 2:** Run both. Expected: FAIL.
- [ ] **Step 3:** Implement. Keep `neonSessionUser` and `getOwnerId` injectable for the test (a default-parameter seam is fine).
- [ ] **Step 4:** Run both, then `npm test` and `npm run typecheck`. Expected: PASS.
- [ ] **Step 5:** Commit: `Sign in through Neon Auth on Postgres setups`.

### Task 7: `/api/db` with the browser allowlist

**Files:**
- Create: `web/src/db/browser-access.ts`, `web/src/db/api.ts`
- Modify: `web/src/api.ts` (route `POST /api/db` after `requireUser`, Postgres only), `web/src/db/postgres.ts` (enforce `scope.access`)
- Test: `web/src/db/browser-access.test.ts`

**Interfaces:**
- Produces: `type BrowserAccess = Record<string, Array<QueryPlan["op"]>>` and `BROWSER_ACCESS: BrowserAccess`:
  - `jobkit_apps`, `jobkit_following`, `jobkit_profile`, `jobkit_jobs`, `jobkit_preference_profile`, `jobkit_job_feedback`, `jobkit_scan_targets`, `jobkit_experience_files`: all five ops.
  - `jobkit_salary_cache`: `select`. `jobkit_scan_runs`: `select`, `insert`.
  - Every other table, including `jobkit_settings`, `jobkit_instance`, `jobkit_external_openings`, `jobkit_sync_state`, `jobkit_schema_migrations`: absent.
- Produces: `handleDbApi(request: Request, env: Env, userId: string): Promise<Response>` in `db/api.ts`. Body: a `QueryPlan` as JSON. Runs it through `createPostgresDb(run, { ownerId: userId, access: BROWSER_ACCESS })` and returns the `DbResult` as JSON. A plan outside `BROWSER_ACCESS` → 403 `{ error }`; a malformed plan or compile error → 400.

- [ ] **Step 1: Write the failing tests:**
  - `access mirrors RLS`: parse every `create policy … on <table> for <op>` in the Supabase SQL files; tables with `for all` must have all five ops in `BROWSER_ACCESS`, `for select`/`for insert` exactly those; tables with RLS and no policy must be absent. This pins the map so a new table stays closed until someone opens it.
  - `handleDbApi` on PGlite: select on `jobkit_settings` → 403; delete on `jobkit_salary_cache` → 403; select on `jobkit_apps` returns only the caller's rows; an insert naming another `user_id` is stored under the caller.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run, `npm test`, `npm run typecheck`. Expected: PASS.
- [ ] **Step 5:** Commit: `Add the browser data endpoint for Postgres setups`.

### Task 8: Browser on Postgres: data client, login, sign-out, reset

**Files:**
- Create: `web/public/jobkit-remote-db.js`
- Modify: `web/public/jobkit-db.js` (realtime only when `typeof client.channel === "function"`; otherwise reload lists after writes and on `visibilitychange` to visible), `web/public/index.html` (client creation around line 1213), `web/public/login.html`
- Test: `web/src/db/remote-client.test.ts`

**Interfaces:**
- Produces, in `jobkit-remote-db.js`: `export function createRemoteClient(fetchImpl = fetch)` returning `{ from(table), auth: { getSession(), signOut() } }`. `from()` records the same `QueryPlan` as Task 1 and on `then` sends `POST /api/db`. `auth.getSession()` calls `GET /api/session` and returns `{ data: { session: { user: { id } } | null } }`; `signOut()` posts to `/api/auth/sign-out`.
- Produces: `GET /api/session` in `api.ts` (after `requireUser`) → `{ userId }`.
- `index.html`: if `window.__JOBKIT_DB?.mode === "postgres"`, build `client` with `createRemoteClient()` instead of importing `@supabase/ssr`; the rest of the page uses `client` as now.
- `login.html` on Postgres: posts `{ email, password }` to `/api/auth/sign-in/email`; shows a **Forgot password?** link that posts `{ email, redirectTo: location.origin + "/login?reset=1" }` to `/api/auth/request-password-reset`, and on `?reset=1&token=…` shows a new-password form posting `{ newPassword, token }` to `/api/auth/reset-password`. These are Better Auth's endpoint names; confirm them against the live Neon Auth before wiring, and adjust here if Neon's differ.

- [ ] **Step 1: Write the failing test** `remote-client.test.ts` (imports the `.js` module under `tsx`): a chain `from("jobkit_apps").update({ status: "x" }).eq("id", "a").select("id")` sends exactly the `QueryPlan` `{ table, op: "update", patch, filters: [{ column: "id", op: "eq", value: "a" }], returning: "id", order: [] }` and resolves to the response body.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement the client, `/api/session`, and the page changes.
- [ ] **Step 4:** Run, `npm test`, `npm run typecheck`. Expected: PASS.
- [ ] **Step 5:** Commit: `Run the tracker page and login on Postgres setups`.

### Task 9: Engine endpoints

**Files:**
- Create: `web/src/engine-api.ts`
- Modify: `web/src/api.ts` (route `/engine/*` before `requireUser`)
- Test: `web/src/engine-api.test.ts`

**Interfaces:**
- Produces: `handleEngineApi(request: Request, env: Env, path: string): Promise<Response | null>`. Auth: `Authorization: Bearer <JOBKIT_ENGINE_TOKEN>` compared with `safeEqual` (move it from `api.ts` to `auth.ts` and export it); missing or unset token → 401. Uses `serviceDb(env)`, so it works on both modes. Endpoints mirror `jobkit/jobkit_web.py`'s `Client`:
  - `GET /engine/owner` → `{ ownerId }`
  - `GET /engine/apps` → `{ apps: [{ id, record, updated_at }] }` for the owner
  - `POST /engine/apps` `{ id, record }` → 201
  - `PATCH /engine/apps/:id` `{ updated_at, record }` → `{ updated: boolean }` (only when `updated_at` still matches)
  - `DELETE /engine/apps/:id?updated_at=…` → `{ deleted: boolean }` (same rule)
  - `POST /engine/openings` `{ rows: [...] }` → `{ upserted: n }` into `jobkit_external_openings` on `user_id,external_key`
  The `record` shape is the one `jobkit_web.py` builds today (`_record`, `insert_app`); read it there and keep field names identical.

- [ ] **Step 1: Write the failing tests** on PGlite with `JOBKIT_ENGINE_TOKEN=t`: no or wrong token → 401; insert then list returns the app; stale `updated_at` on PATCH → `{ updated: false }` and the row is unchanged; current one → `{ updated: true }`; openings upsert twice → one row.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run, `npm test`, `npm run typecheck`. Expected: PASS.
- [ ] **Step 5:** Commit: `Add engine endpoints for sync web on any setup`.

### Task 10: Setup wizard on Postgres

**Files:**
- Modify: `web/src/setup.ts` (`handleSetup`), `web/public/setup.html`, `web/public/setup.js` (database and account steps, around lines 171, 183, 281)
- Test: `web/src/setup-postgres.test.ts`

**Interfaces:**
- `GET /api/setup/state` on Postgres adds `db: { mode: "postgres", host, pendingMigrations: string[], neonAuth: { reachable: boolean; originTrusted: boolean; origin: string } }`. `host` is the host name from `DATABASE_URL` only. `originTrusted` is false when a `POST <NEON_AUTH_BASE_URL>/sign-in/email` with an empty body and `Origin: <request origin>` answers `INVALID_ORIGIN`.
- `POST /api/setup/prepare-db` → `{ applied: string[] }` via `applyMigrations`. Allowed only while no owner exists, or for the signed-in owner.
- `POST /api/setup/owner` (Postgres only) → verifies the caller with `neonSessionUser`; 409 if an owner exists; 403 if `SETUP_TOKEN` is set and the body's `setupToken` doesn't match; otherwise writes `jobkit_instance` `(id 1, owner_user_id = userId)` and returns `{ ownerId }`.
- `setup.js` on Postgres: the database step shows the host, a **Prepare database** button, and, when `originTrusted` is false, the message "Add <origin> to Neon Auth's trusted domains: Neon console, Settings, Better Auth, Domains. Then click Check again." The account step signs up via `/api/auth/sign-up/email`, signs in via `/api/auth/sign-in/email`, calls `/api/setup/owner`, then shows: "Now switch off Sign-up with Email in the Neon console (Settings, Better Auth) so nobody else can create an account."

- [ ] **Step 1: Write the failing tests** (stub `neonSessionUser` and the Neon Auth origin probe): state reports `originTrusted: false` on `INVALID_ORIGIN`; owner claim succeeds once, second claim → 409; with `SETUP_TOKEN=s`, wrong token → 403; `prepare-db` after an owner exists without a session → 401; state never contains `DATABASE_URL` or its password (assert on the serialized response).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run, `npm test`, `npm run typecheck`. Expected: PASS.
- [ ] **Step 5:** Commit: `Set up a Postgres install from the wizard`.

### Task 11: Docs and config examples

**Files:**
- Modify: `web/README.md`, `web/.dev.vars.example`, `web/wrangler.local.example.jsonc`, root `README.md` if it describes the web setup

- [ ] **Step 1:** README: add "Pick a setup" (the spec's table, setups 1 and 3 available now, 2 marked "coming soon"), an install section for "Cloudflare + Neon" (create the database from Vercel Storage with Auth on, or at neon.tech with Neon Auth enabled; set `JOBKIT_DB=postgres`, `DATABASE_URL` as a secret, `NEON_AUTH_BASE_URL` as a var, optional `JOBKIT_ENGINE_TOKEN` as a secret; add the site's address to Neon Auth's trusted domains; run `/setup`; switch off sign-up after claiming the owner; optionally set a custom email provider for reset emails), "Switching setups" (install the new one, run `sync web`; jobkit does not move data), and the four new variables in the Configuration table with Supabase-only variables marked.
- [ ] **Step 2:** Add the new variables, commented, to `.dev.vars.example` and `wrangler.local.example.jsonc`.
- [ ] **Step 3:** Commit: `Document the Cloudflare + Neon setup`.

### Task 12: End-to-end check on a real setup 3

No code. Done by the owner with the agent, against real services; nothing merges until this passes.

- [ ] **Step 1:** Create a Neon database with Auth from Vercel Storage; add the Worker's address to trusted domains.
- [ ] **Step 2:** Deploy the branch to a test Worker with `JOBKIT_DB=postgres`, `DATABASE_URL`, `NEON_AUTH_BASE_URL`, `JOBKIT_ENGINE_TOKEN`.
- [ ] **Step 3:** Run `/setup` end to end: prepare database, claim owner, switch off sign-up, finish the wizard. Expected: the tracker opens.
- [ ] **Step 4:** Add, edit (twice from two tabs to hit the "changed elsewhere" path) and delete an application; run a scan from the UI; open a job's CV. Expected: all work, no console errors.
- [ ] **Step 5:** Sign out, use **Forgot password?**, set a new password from the email, sign in. Expected: back in.
- [ ] **Step 6:** `curl` `/api/engine/apps` with and without the token. Expected: data, then 401.
- [ ] **Step 7:** Point a setup-1 build (no `JOBKIT_DB`) at a working Supabase project and click through the tracker. Expected: unchanged behavior. If no Supabase project is available (the owner's was down in October 2026), say so in the PR and rely on the unchanged-test evidence from Task 4.
