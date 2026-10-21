# Sync the jobkit engine with the web app

Date: 2026-10-21
Status: draft, awaiting owner approval

## Intent

The web app (`web/`, Cloudflare Workers plus the user's own Supabase) and the Python engine (`jobkit/`, run locally and by Claude skills) each keep an application tracker, and today they don't talk. Two gaps follow:

1. Applications logged through the engine (`/jobkit:tailor-cv`, `/jobkit:log-application`, `jobkit add` / `log`) never reach the web app, and web edits never reach `applications.md`.
2. The web scanner only reads Ashby, Greenhouse, Lever, Workable, Remote.com and Teamtailor. The engine already reads many more boards (SuccessFactors, Workday, SmartRecruiters, Avature, Taleo, Cornerstone, Oracle, join.com and custom ones), but nothing feeds those postings to the web app.

This connects the two. The engine keeps doing what it does well, reading boards and editing the markdown, and the web app stays the one place where postings are scored and browsed.

## Goals

- Two-way sync between `applications.md` and the web app's `jobkit_apps`, with the engine's existing three-way merge rules.
- The engine pushes postings from its boards into the web app, and the web app's daily scan scores them like its own.
- Everything runs without a laptop: a GitHub Actions workflow in the user's private data repo, run daily.
- A user who doesn't use the web app sees no change.

## Non-goals

- Porting the engine's board readers to TypeScript or Cloudflare.
- Moving Telegram alerts. The local daily poll and its alerts stay exactly as they are.
- Syncing the watchlist, the master CV, or preferences.
- Deleting applications through sync (neither side supports it today).

## Anti-goals

- A web edit silently undone by the engine, or the reverse.
- A machine whose copy is merely behind undoing another machine's edit.
- The Supabase secret key in any committed file.
- New Python dependencies (the engine is stdlib only, Python 3.9+).
- Engine commands failing because Supabase is unreachable.

## Decisions (with the owner)

1. The two-way sync and the posting push live in the jobkit engine. The scheduled runner is GitHub Actions in the owner's private data repo.
2. Telegram stays unchanged for now.
3. The merge snapshot ("state") lives in Supabase, not in a file, one per machine (`jobkit_sync_state` keyed by user and machine). A first draft shared one snapshot; a test showed that a copy of `applications.md` that is merely behind then reads as an edit back to the old value and undoes the other machine's change.
4. Delivered as two jobkit PRs (A: web, B: engine) plus a commit in the data repo.

## Design

### PR A: web (`web/`)

**Migration** `jobkit_engine_bridge`:

- `jobkit_external_openings`: `user_id`, `external_key` (text, primary key with `user_id`), `company`, `title`, `url`, `description`, `location`, `source` (the engine's board id, e.g. `successfactors:jobs.edp.com`), `found_at timestamptz default now()`. RLS on, no policies: only the service role (the Worker and the engine) touches it.
- `jobkit_sync_state`: `user_id`, `machine` (primary key together), `state jsonb not null default '{}'`, `updated_at`. One merge snapshot per machine that syncs, in the same shape as `.jobkit-sync-state.json`. RLS on, no policies.
- A trigger moves `jobkit_apps.updated_at` on every update (the web app's writes never set it), so the engine's conditional updates can see web edits.
- `npm run build:sql` regenerates `public/setup/jobkit-schema.sql`.

**Scan** (`src/scan/pipeline.ts`, `runJobScan`): after `fetchAllSources`, read the user's rows from `jobkit_external_openings`, map them to the same raw-opening shape the sources return (`source: "engine"`), and add them to the fetched list. They then go through the existing exclusion, prefilter, duplicate check (by `external_key` and by URL against jobs and applications), Claude scoring and salary. After the scan stores its results, delete the external rows it read, whether they were kept or filtered out, so each one is considered once.

### PR B: engine (`jobkit/`)

**Config**: `[tracker] backend = "web"`, `supabase_url`. The secret key is `SUPABASE_SECRET_KEY` from the environment, or from an env file named by `supabase_env` (default `~/.jobkit/supabase.env`), never `jobkit.toml`. The owner user id is read from `jobkit_instance` at runtime. Existing `artifact_url` configs keep working.

**Supabase client** (`jobkit_web.py`): stdlib `urllib` against PostgREST with the secret key. It covers:
- read and write `jobkit_apps`
- read and write the user's `jobkit_sync_state` row
- upsert into `jobkit_external_openings`
- read the owner id

**`jobkit sync web`**:
1. Pull `jobkit_apps` and the snapshot from `jobkit_sync_state`.
2. Merge with `applications.md` using the existing three-way rules.
3. Push the rows that changed. Updates are conditional on `updated_at` as read; a miss means a web edit landed mid-sync, so it pulls again and retries, up to 3 times.
4. Write `applications.md` and the new snapshot only once every push landed.

**Automatic sync**: with `backend = "web"`, `add` and `log` sync before and after (reads: before only). Offline or no key gives one warning line and the command runs on the local file.

**`jobkit watch push-postings`**: for every watchlist company whose board the web app can't read (anything but Ashby, Greenhouse, Lever, Workable, Remote.com and Teamtailor), fetch current postings with the engine's adapters, apply the watchlist's include and exclude keywords and location filter, and upsert them into `jobkit_external_openings`. The key is `<source>:<posting id>`. It doesn't touch `.jobkit-watch-state.json` or send Telegram alerts.

**Docs and skill**: README section, `sync-tracker` skill branch for `backend = "web"`, and a copy-paste workflow `templates/github-actions/jobkit-daily.yml`.

### Data repo (the user's private repo)

- `.github/workflows/jobkit-daily.yml` runs daily (05:30 UTC, before the Worker's 07:00 scan) and on demand:
  1. Check out the repo, set up Python 3.12, clone jobkit `main`.
  2. Run `watch push-postings`, then `sync web`.
  3. If `applications.md` changed, commit it as `jobkit-bot`.
- `jobkit.toml` gets the `[tracker]` web settings.
- The owner adds the `SUPABASE_SECRET_KEY` repository secret.

## Testing

- **PR A:** unit tests for mapping external rows to openings and for deleting them after a scan. The migration is checked through the regenerated setup SQL.
- **PR B:** stdlib `unittest` with faked HTTP, covering:
  - the merge with the snapshot from Supabase
  - a conditional-update miss followed by a retry
  - no write to `applications.md` or the snapshot when a push fails
  - the offline warning
  - push-postings filtering and keys

  The existing suite must stay green.
- **End to end:** after merge, run `sync web` and `push-postings` once by hand against the owner's project, then trigger the workflow.

## Left to the builder

Function names, PostgREST details, and how the scan's existing helpers are reused, as long as the guarantees above hold.
