---
name: sync-tracker
description: Use when applications.md changed in a session, at the start of a session that touches the tracker, or when the user wants their application tracker artifact (a shareable dashboard on claude.ai) set up or refreshed.
---

# Sync with the tracker artifact

`applications.md` and the tracker artifact (database collections `apps` and `following`) are kept in two-way sync. The artifact database is only reachable from a Claude session (the ArtifactData tool), so this skill runs the transport and the engine does the merge. Run engine commands as `python3 ~/.jobkit/launcher.py <command>` from the data repo; use a scratch directory for the exports.

## First time (no `[tracker] artifact_url` in jobkit.toml)

Ask the user first. Then load the artifact-capabilities skill, publish `$(python3 ~/.jobkit/launcher.py plugin-root)/artifact/tracker.html` as a new artifact with the `db` capability, and write its URL to `artifact_url` in `jobkit.toml`. Continue with the sync below.

## Sync

1. ArtifactData `list` on `apps` with `query {"limit":1000}` and an `out_dir`. Write `{"<doc id>": <version>}` for every document in the listing to a versions JSON file.
2. `python3 ~/.jobkit/launcher.py sync sync --db-dir <out_dir>/apps --push-dir <dir> --versions <file>` merges both ways, rewrites `applications.md`, and prints the `writes` still needed in the artifact.
3. Send `writes` as one ArtifactData `batch` (fill any `needs_version` from the versions file), then `python3 ~/.jobkit/launcher.py sync commit`. Commit only after the batch succeeds; a failed push is retried next run.
4. Refresh the Following panel: `python3 ~/.jobkit/launcher.py watch follow-postings` (follows applications added in the artifact), ArtifactData `list` on `following` with an `out_dir`, then `python3 ~/.jobkit/launcher.py sync following --push-dir <dir> [--current <out_dir>/following/all.json --version N]`, and send the write it prints, if any.

Merge rule: three-way against `.jobkit-sync-state.json`. A field changed on one side takes that side; changed on both, the later Last contact wins, ties go to `applications.md`. Events are unioned. Rows match on (Company, Role).
