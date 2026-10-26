# PR 2: the engine's `sync web` client for any setup, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `jobkit sync web` and `watch push-postings` work against a Neon setup by talking to the web app's own `/api/engine/*` endpoints, while existing Supabase configs keep working unchanged.

**Architecture:** A second client class in `jobkit/jobkit_web.py`, `EngineClient`, with exactly the methods of today's `Client` (which talks to Supabase's REST API). `client_from_config` picks `EngineClient` when `[tracker] web_url` is set, otherwise `Client`. The row-to-record mapping both clients need moves into one shared function. The token comes from `JOBKIT_ENGINE_TOKEN` or an env file, never `jobkit.toml`.

**Tech Stack:** Python 3.9+, stdlib only (`urllib`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-26-backend-choice-design.md`, section "Engine bridge (`sync web`)", Delivery item 2. Builds on PR 1 ([diogonmoura/jobkit#16](https://github.com/diogonmoura/jobkit/pull/16)); branch `engine-web-url` from `backend-choice-design`, PR based on it (stacked).

## Global Constraints

- Stdlib only, Python 3.9+. Tests: `python3 -m unittest discover -s jobkit -p 'test_*.py'` from the repo root; all must pass at every commit.
- The engine token is never read from or written to `jobkit.toml`. Sources, in order: env var `JOBKIT_ENGINE_TOKEN`, then the env file named by `[tracker] web_env` (default `~/.jobkit/web.env`), same file format `supabase_secret_key` already reads (`NAME=value`, optional `export `, optional quotes).
- Existing configs (`backend = "web"` with `supabase_url`, no `web_url`) behave exactly as today.
- When both `web_url` and `supabase_url` are set, `web_url` wins.
- The engine API (from PR 1, `web/src/engine-api.ts`), with `Authorization: Bearer <token>`:
  - `GET /api/engine/owner` → `{ "ownerId": "<uuid>" }`, or 404 `{ "error": ... }` before setup
  - `GET /api/engine/apps` → `{ "rows": [ <raw jobkit_apps rows, all columns> ] }`
  - `POST /api/engine/apps` body `{ "id": ..., <record fields> }` → 201 `{ "inserted": true }`; 409 when the id exists
  - `PATCH /api/engine/apps/<id>?updated_at=<value>` body `<record fields>` (never `id` or `updated_at`) → `{ "updated": bool }`
  - `DELETE /api/engine/apps/<id>?updated_at=<value>` → `{ "deleted": bool }`
  - `POST /api/engine/openings` body `{ "rows": [...] }` → `{ "upserted": n }`
  - 401 for a missing or wrong token; errors are JSON `{ "error": "<message>" }`
  The server forces the owner on every write, so `EngineClient` never sends `user_id`.
- `updated_at` is opaque: passed back exactly as read, URL-encoded. Never parsed.
- Plain voice, no em dashes in docs and messages. Commit messages: plain imperative subject, then the `Co-Authored-By` trailer.

## Review Focus

1. **The 409 retry.** `jobtrack_sync._insert_web_app` retries with a suffixed id only when `WebError.status == 409`. `EngineClient.insert_app` must raise `WebError` with `status=409` on a duplicate. Pinned in Task 2.
2. **A setup that isn't finished.** `GET /owner` answers 404; the user must see "no owner yet; finish /setup in the web app first", same as today. Pinned in Task 2.
3. **A token in the wrong place.** If `jobkit.toml` has a `web_token` (or similar) key, it must be ignored, not used. Pinned in Task 1.
4. **Trailing slashes and paths in `web_url`.** `https://job.example.com/` and `https://job.example.com` must both work; ids with spaces or slashes are URL-encoded in the path. Pinned in Task 2.
5. **Network failures.** Timeouts, DNS errors and non-JSON bodies become a `WebError` with a short reason, exactly like `Client._call`, so `auto_sync_web` warns once and carries on. Pinned in Task 2.

---

### Task 1: Config: `web_url`, `web_env` and the engine token

**Files:**
- Modify: `jobkit/jobkit_config.py`
- Test: `jobkit/test_jobkit_config.py`

**Interfaces:**
- Produces: `Config.web_url: str = ""`, `Config.web_env: str = "~/.jobkit/web.env"`, `ENGINE_TOKEN_VAR = "JOBKIT_ENGINE_TOKEN"`, and `engine_token(config: Config) -> str` (env var first, then the env file, else `""`). `supabase_secret_key` and `engine_token` share one private helper that reads a named variable from an env file.

- [ ] **Step 1: Write the failing tests** in `test_jobkit_config.py`: `web_url` and `web_env` load from `[tracker]`; defaults are `""` and `~/.jobkit/web.env`; `engine_token` prefers the env var; falls back to the file (with `export ` and quotes); returns `""` when neither exists; a `web_token = "x"` key in `jobkit.toml` is ignored. Existing `supabase_secret_key` tests still pass unchanged.
- [ ] **Step 2:** Run `python3 -m unittest jobkit.test_jobkit_config` (or the discover command). Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full discover command. Expected: PASS.
- [ ] **Step 5:** Commit: `Read the web app URL and engine token from config`.

### Task 2: `EngineClient` and client selection

**Files:**
- Modify: `jobkit/jobkit_web.py`
- Test: `jobkit/test_jobkit_web.py`

**Interfaces:**
- Consumes: `jobkit_config.engine_token`, `Config.web_url`.
- Produces: `class EngineClient` with `__init__(self, url: str, token: str, opener=urllib.request.urlopen, timeout: int = 20)` and the same public methods and return types as `Client`: `owner_id() -> str`, `pull_apps(user_id) -> DbDocs` (also setting `web_only_keys` and `hidden_keys`), `insert_app(user_id, app_id, record) -> None`, `delete_app_if_unchanged(user_id, app_id, updated_at) -> bool`, `update_app_if_unchanged(user_id, app_id, updated_at, record) -> bool`, `upsert_openings(user_id, rows) -> None` (no call for an empty list). `user_id` arguments are accepted and ignored.
- The mapping in today's `Client.pull_apps` (statuses, web-only columns, `_record`) moves into one function both clients call, with no change to its behavior.
- `client_from_config(config, timeout=20)`: `web_url` set → `EngineClient` (error `WebError("no engine token")` when the token is empty); else today's Supabase path; neither URL → `WebError("no web_url or supabase_url in jobkit.toml")`.

- [ ] **Step 1: Write the failing tests** with the existing `FakeOpener`:
  - each method sends the right method, URL (base with or without trailing slash; `?updated_at=` URL-encoded; ids URL-encoded in the path), `Authorization: Bearer <token>` header and JSON body, and returns the right value from the canned response;
  - `pull_apps` over raw rows gives the same `DbDocs`, `web_only_keys` and `hidden_keys` as `Client.pull_apps` for the same rows (use the existing `ROW` fixture plus a `considering` row and one with `cv_md`);
  - `insert_app` on a 409 raises `WebError` with `status == 409`;
  - `owner_id` on a 404 raises `WebError("no owner yet; finish /setup in the web app first")`;
  - a 401 raises `WebError` with `status == 401`; a timeout, a URLError and a non-JSON body each raise `WebError` with a short reason;
  - `client_from_config`: `web_url` + token → `EngineClient`; `web_url` without token → `WebError("no engine token")`; only `supabase_url` → `Client` (unchanged); both URLs → `EngineClient`; neither → the new message.
- [ ] **Step 2:** Run the web tests. Expected: FAIL.
- [ ] **Step 3:** Implement. Reuse `Client._call`'s error handling by sharing it (a small base or helper), not by copying it.
- [ ] **Step 4:** Run the full discover command. Expected: PASS.
- [ ] **Step 5:** Commit: `Sync with the web app through its engine API`.

### Task 3: Docs, skill hint and the Action template

**Files:**
- Modify: `README.md` (the "use it alongside this plugin" paragraph and the `[tracker]` sample), `skills/sync-tracker/SKILL.md` (the error hint), `templates/github-actions/jobkit-daily.yml` (comment and env), `web/README.md` (replace "sync web for Neon setups is coming" with how to set it up)

- [ ] **Step 1:** README: two ways to point the engine at the web app. Any setup: `web_url = "https://<your jobkit site>"` plus `JOBKIT_ENGINE_TOKEN` (env or `~/.jobkit/web.env`), the same value as the Worker's `JOBKIT_ENGINE_TOKEN` secret. Supabase only, as today: `supabase_url` plus `SUPABASE_SECRET_KEY`. Say `web_url` wins when both are set. Add `web_url` and `web_env` to the `[tracker]` sample.
- [ ] **Step 2:** `SKILL.md`: the usual errors are a missing `JOBKIT_ENGINE_TOKEN` or `web_url` (or, on the older setup, `SUPABASE_SECRET_KEY` or `supabase_url`).
- [ ] **Step 3:** Action template: pass `JOBKIT_ENGINE_TOKEN: ${{ secrets.JOBKIT_ENGINE_TOKEN }}` alongside the existing `SUPABASE_SECRET_KEY` line, and update the header comment to describe both.
- [ ] **Step 4:** Run the full discover command (`test_plugin_files.py` checks plugin files). Commit: `Document sync web through the engine API`.

### Task 4: Live check against the throwaway Worker

No code. Run by the controller against `https://jobkit-livecheck.diogomoura03.workers.dev` (its token is in the session scratchpad), from a temporary data folder, never the owner's real data repo.

- [ ] **Step 1:** In a temp folder: a `jobkit.toml` with `backend = "web"` and `web_url` set, and an `applications.md` with two sample applications. Export `JOBKIT_ENGINE_TOKEN`.
- [ ] **Step 2:** `JOBKIT_HOME=<temp folder> python3 jobkit/cli.py sync web` (`jobkit_paths` reads `JOBKIT_HOME`, else the working directory). Expected: both applications pushed; `GET /api/engine/apps` lists them.
- [ ] **Step 3:** Run it again. Expected: nothing to push, `applications.md` unchanged.
- [ ] **Step 4:** Change one application's status through the web app (`/api/db` from the signed-in pane, as in PR 1's live check), sync again. Expected: `applications.md` picks up the change.
- [ ] **Step 5:** Run with a wrong token. Expected: a one-line error naming the HTTP 401, no traceback.
