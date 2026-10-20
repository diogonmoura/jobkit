---
name: log-application
description: Use when the user reports news on a job application (a reply, screening call, interview, offer, rejection, withdrawal) or asks what needs follow-up or how their search is going.
---

# Log application events

Run commands as `python3 ~/.jobkit/launcher.py <command>` from the data repo.

- Record an event: `log --company "<Company>" --role "<Role>" --text "<what happened>" [--date YYYY-MM-DD]`. Company and role must match the row in `applications.md` exactly; read the table first if unsure.
- Change a status: edit the Status cell of that row in `applications.md` (one of `applied`, `ack`, `screening`, `interview`, `offer`, `rejected`, `withdrawn`, `ghosted`), then log an event describing the change.
- What needs follow-up: `stale`. Response rates and timings: `stats`. Local web UI: `serve`.
- If `[tracker] artifact_url` is set in `jobkit.toml`, run `/jobkit:sync-tracker` after any change.
