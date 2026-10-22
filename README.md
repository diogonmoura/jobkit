# jobkit

A Claude Code plugin for job searching. It tailors your CV to a job posting
and renders it as an ATS-safe PDF, files it, tracks your applications, and
alerts you when companies you follow post new roles. Claude does the writing
with your own subscription; jobkit supplies the workflow, the template and
the checks.

## Install

1. `brew install typst poppler` (Typst renders the PDF; poppler's `pdftotext`
   checks it the way applicant tracking systems read it). Python 3.9+ is the
   only other requirement.
2. In Claude Code:

   ```
   /plugin marketplace add diogonmoura/jobkit
   /plugin install jobkit@jobkit
   ```

3. Make an empty folder for your data (ideally a private git repo), open
   Claude Code there and run `/jobkit:init`. It scaffolds the folder, installs
   `~/.jobkit/launcher.py`, and helps you turn your existing CV into `cv.md`.

## Skills

| Skill | What it does |
|---|---|
| `/jobkit:init` | Set up a data repo and convert your CV into `cv.md` |
| `/jobkit:tailor-cv <posting url>` | Reword `cv.md` for a posting, render, ATS-check, file under `Applications/`, log it |
| `/jobkit:log-application` | Record replies, interviews and status changes; see what needs follow-up |
| `/jobkit:follow-company` | Watch a company's job board for new roles |
| `/jobkit:build-connector` | Add support for a job board jobkit cannot read yet |
| `/jobkit:sync-tracker` | Two-way sync with your tracker dashboard on claude.ai |

## Hosted tracker (optional)

[`web/`](web/README.md) is a self-hosted, single-user web app (Cloudflare
Workers + your own Supabase project) for the same job search: a daily scan of
the boards you watch (Ashby, Greenhouse, Lever, Workable, Remote.com) scored
against your own rubric by an LLM, a tailored CV per posting with Word and PDF
export, drafted application answers, and an application tracker. A first-run
wizard at `/setup` connects the database and an AI model (OpenRouter by
default) and turns your preferences into the profile, rubric and filters, so
no personal data lives in the code. See [`web/README.md`](web/README.md).

To use it alongside this plugin, set `[tracker] backend = "web"` and
`supabase_url` in `jobkit.toml` and put the Supabase secret key in
`SUPABASE_SECRET_KEY` (or the env file named by `supabase_env`, default
`~/.jobkit/supabase.env`; never in `jobkit.toml`). Then:

- `sync web` keeps `applications.md` and the web app's applications in
  two-way sync, with the same merge rules as the artifact tracker. `add`, `log`,
  `stale` and `stats` sync on their own; offline they warn once and carry on.
  Each machine keeps its own sync snapshot next to `applications.md`
  (`.jobkit-web-sync-<machine>.json`, named by `JOBKIT_MACHINE`,
  `[tracker] machine`, or the hostname).
- `watch push-postings` sends current postings from watched boards the web app
  can't read (SuccessFactors, Workday, SmartRecruiters and the rest) to it; its
  daily scan scores them like its own.
- [`templates/github-actions/jobkit-daily.yml`](templates/github-actions/jobkit-daily.yml)
  runs both every morning from your private data repo, so no laptop needs to
  be on.

## Commands

Skills call these for you. Run them yourself as
`python3 ~/.jobkit/launcher.py <command>` from your data repo.

| Command | Purpose |
|---|---|
| `init --name "Full Name"` | Scaffold a data repo in the current folder (never overwrites) |
| `render FILE.md [-o OUT.pdf]` | Render a CV to PDF |
| `ats-check FILE.pdf --source FILE.md` | Check the PDF reads cleanly, in order, as ATS parsers see it |
| `file-cv --company C --pdf PDF [--date D]` | Copy a PDF to `Applications/<date> - <Company>/` under your configured name |
| `add --company C --role R [--cv NAME] [--posting URL] [--applied DATE] [--no-follow]` | Log an application. With a posting link, the company is also followed |
| `log --company C --role R --text "..." [--date DATE]` | Append a dated event and move Last contact forward |
| `stale` / `stats` | Applications gone quiet (`applied` past 14 days, active past 10) / response rates. `--json` for machine output |
| `serve [--port 8765]` | Local web UI (loopback only, token-protected) |
| `watch add --url URL` / `remove --company C` / `list` / `status` | Manage watched job boards |
| `watch follow-postings [--retry]` | Follow every applied-to company whose posting link has not been tried yet |
| `watch poll [--dry-run]` | Fetch all boards and notify new matches |
| `schedule install` / `uninstall` | Run `watch poll` daily at 8:30 (LaunchAgent on macOS, a cron line elsewhere) |
| `doctor` | Check this machine and data repo |
| `plugin-root` | Print where the plugin is installed |

`applications.md` is the source of truth and is meant to be edited by hand.
The UI re-reads it on every request. Backfilling an older event never moves
Last contact backwards.

## Your data repo

```
cv.md                         master CV
tailored/<company>.md|.pdf    tailored versions
Applications/<date> - <Company>/CV_<Name>.pdf
applications.md, watchlist.md   tracker and watched boards (hand-editable)
jobkit.toml                     settings
connectors_local.py             optional: your own job board connectors
```

`jobkit.toml`:

```toml
[user]
pdf_name = "CV_JaneDoe.pdf"

[voice]
rules = ["No em dashes.", "Plain voice, no buzzwords."]

[notify]
channel = "console"    # or "telegram"
telegram_env = "~/.claude/channels/telegram/.env"

[tracker]
backend = ""            # "artifact", "web", or empty for none
artifact_url = ""       # backend = "artifact": the claude.ai tracker page
supabase_url = ""       # backend = "web": https://<ref>.supabase.co
```

## CV data

Your CV is a Markdown file that follows one fixed convention, so jobkit can
tell a job title from its dates the way an ATS needs to:

```markdown
# Jane Doe
+351 900 000 000 | jane@example.com | [linkedin.com/in/janedoe](https://linkedin.com/in/janedoe) | Porto, Portugal

## Professional Profile
One paragraph. It may wrap over several lines.

## Professional Experience

### Head of Strategy
Example Corp, Portugal | 2021 - Present
- **Team Leadership:** Leads a team of 7.
- A bullet without a label.

## Education

### MSc Management
University of Porto, Porto | 2013 - 2015

## Skills
- **Strategy:** Corporate strategy, M&A, Low-Code Automation (Power Automate, n8n)

## Languages
- Portuguese (native)
- English (fluent)
```

- "Professional Profile", "Professional Experience", "Education" and "Skills"
  are fixed headings. Any other `##` heading becomes an extra section after Skills.
- The line under each `### Title` is `Organisation, Location | Dates`; the
  location is whatever follows the last comma (`Organisation | Dates` for none).
- Commas inside parentheses stay in one skill item.
- Tables, `####` headings, numbered lists, images and HTML are rejected with a
  line number, because the PDF could not show them ATS-safely. Missing pieces
  are reported by field (`experience[1].dates: missing`).

The layout always comes from the template, so the PDF looks the same however
the Markdown is spaced.

## ATS rules the template keeps

Workday, Greenhouse, iCIMS, Taleo and Lever extract PDF text in stream order,
not visual layout. The template (`templates/cv.typ`) therefore:

- never uses tables, grids or columns: each job is a bold title line, then a
  plain `Company, Location | Dates` line, then bullets;
- uses the headings parsers look for: "Professional Profile",
  "Professional Experience", "Education", "Skills";
- puts nothing in the page header or footer;
- inserts your text literally (Typst markup characters are never evaluated).

`ats-check` verifies every rendered PDF with `pdftotext` (no `-layout`): each
heading, title, entry line and bullet must appear once, in source order.

## Watching job boards

`watch add --url URL` detects the board: `greenhouse`, `lever`, `lever-eu`,
`ashby`, `smartrecruiters`, `successfactors`, `workday`, `taleo` (Business
Edition), `teamtailor`, `oracle`, `cornerstone`, `join`, `avature`,
`bendingspoons`, `mantu` and `itjobs`. LinkedIn and sites that block automated
access are refused by name. For anything else, run `/jobkit:build-connector`.
Current postings are seeded as seen, so only roles posted from now on alert.

Filters live in `watchlist.md`: global `- include:`, `- exclude:` and
`- location:` bullets, and per-company Keywords and Location columns
(comma-separated). A posting whose board reports no location always passes the
location filter. SuccessFactors boards often report `"<City>, <ISO2>"` (e.g.
`Lisbon, PT`); a leading comma cannot anchor a match because the column is
comma-separated, so use the bare code (`PT`). RMK-style SuccessFactors boards
use ISO3 codes (`PRT`), so give such a company its own Location.

### Following companies you applied to

`add --posting URL` resolves the posting to its job board and puts the company
on the watchlist. A board already watched is not added twice. If the board has
no connector, the application is still added and the reason is printed. Every
outcome is recorded in `.jobkit-follow.json`, keyed by posting URL, so each link
is tried once. Applications added from the tracker dashboard are followed by
`watch follow-postings`, which the sync runs.

Notifications print to the console by default. For Telegram, set
`[notify] channel = "telegram"` in `jobkit.toml`; the bot token is read at
runtime from the env file named by `telegram_env` (`TELEGRAM_BOT_TOKEN=...`),
and the chat id from `access.json` next to it. Neither is ever stored in your
repo.

## Development

```bash
python3 -m unittest discover -s jobkit -p 'test_*.py'
```

Rendering tests are skipped when `typst` or `pdftotext` is missing. Set
`JOBKIT_PLUGIN_ROOT=~/Development/jobkit` to make the launcher use a local
checkout instead of the installed plugin. New job board connectors go in
`jobkit/jobtrack_boards.py` with a pure `parse_*` function and tests built
from a real response sample.
