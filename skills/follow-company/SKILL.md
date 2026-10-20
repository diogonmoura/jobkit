---
name: follow-company
description: Use when the user wants alerts for new roles at a company, wants to stop following one, or asks which companies are watched and whether the watch is healthy.
---

# Follow companies for new roles

Run commands as `python3 ~/.jobkit/launcher.py <command>` from the data repo.

- Follow: `watch add --url "<careers page or any posting URL>" [--company "<Name>"] [--keywords "a, b"]`. Current postings are seeded as seen, so only roles posted from now on alert.
- If it refuses because the board is unsupported, run `/jobkit:build-connector` with the URL. If it names a blocked site (LinkedIn and similar), look for the company's own careers site instead.
- Stop following: `watch remove --company "<Name>"`. List: `watch list`. Health: `watch status`.
- Follow every company applied to with a posting link: `watch follow-postings` (add `--retry` after a new connector lands).
- Filters live in `watchlist.md`: global `- include:` / `- exclude:` / `- location:` bullets, and per-company Keywords and Location columns (comma-separated). A posting with no location always passes the location filter.
- Check now without side effects: `watch poll --dry-run`.
