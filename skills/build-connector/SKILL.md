---
name: build-connector
description: Use when a company's job board is not supported by jobkit (watch add or add reports no connector) and the user wants to follow that company.
---

# Build a job board connector

Run engine commands as `python3 ~/.jobkit/launcher.py <command>`. The engine source is under `$(python3 ~/.jobkit/launcher.py plugin-root)/jobkit/`; read `jobtrack_boards.py` there for the existing connectors and their shape.

## Never

Do not build a connector that has to get past an access control: a bot-protection or CAPTCHA page, a login wall, or a site whose terms forbid automated access (LinkedIn). Tell the user why and stop. If the posting is only on an aggregator, look for the company's own careers site or its company page on the aggregator and follow that instead.

## Steps

1. Probe the site from the shell to find how its own careers page loads roles, and prefer the most structured source that gives a location: a public JSON API, an RSS or JSON feed, page data embedded in the HTML (`__NEXT_DATA__`), a public search index key the page itself uses, or server-rendered HTML with a paging parameter.
2. Decide where it goes: `gh api repos/diogonmoura/jobkit --jq .permissions.push`.
   - `true` (a jobkit maintainer): clone `diogonmoura/jobkit`, add the connector to `jobkit/jobtrack_boards.py` (a resolver from the URL or page, a fetcher that gets every open role, and a pure `parse_*` function), add the vendor to `VENDORS` and `ADAPTERS` in `jobkit/jobtrack_watch.py`, add tests with a small sample of the real response to `jobkit/test_jobtrack_boards.py`, run `python3 -m unittest discover -s jobkit -p 'test_*.py'`, document the board in `README.md`, and open a PR.
   - Otherwise: write `connectors_local.py` in the data repo. It defines `FETCHERS = {"<vendor>": fetch}` where `fetch(source, timeout)` returns a list of `Posting`, and `URL_RESOLVERS = [resolve]` where `resolve(url)` returns `Source("<vendor>", "<slug>")` or `None` (and `PAGE_RESOLVERS` with `resolve(url, page)` if the board is only recognisable from its HTML). Import `Posting` and `Source` from `jobtrack_watch`. Keep parsing in a pure function you can test against a saved response. Then tell the user to share it upstream as a PR to diogonmoura/jobkit.
3. Check it live: `python3 ~/.jobkit/launcher.py watch add --url "<url>"`, then `watch poll --dry-run`, then `watch follow-postings --retry`.
