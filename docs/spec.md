# jobkit: shareable CV tailoring and job tracking plugin

Date: 2026-10-14
Status: approved design, awaiting spec review

## Goal

Other people asked to use the CV tailoring and job tracking workflow that lives in
this repo. Package it as a Claude Code plugin so they can install it, keep
their own data in their own repo, and receive fixes and new job board connectors
as plugin updates. The author's own data repo runs on the same plugin, so
every improvement reaches everyone.

Success: a new user installs the plugin, runs `/jobkit:init`, fills in `cv.yaml`,
and within about 15 minutes has a tailored, ATS-checked PDF and a tracker row for
a real posting.

Later (not in this spec): a hosted MCP server on Vercel/Supabase for users who
only use claude.ai. The engine and Typst template are designed to be reused by it.

## Users and constraints

- Tech-comfortable, already use Claude Code, mostly macOS. Linux should work;
  Windows is not a target.
- Hosted at `diogonmoura/jobkit`. Installed with
  `/plugin marketplace add diogonmoura/jobkit`.
- Python 3.9+, standard library only (as today). External binaries: `typst`
  (rendering) and `pdftotext` from poppler (ATS check).
- No personal data in the plugin repo. It ships a fictional sample CV.
- Each user tailors with their own Claude subscription. The plugin makes no LLM
  calls itself.

## Scope of v1

Everything that exists today, generalised:

1. CV tailoring from structured data, rendered with Typst, with an ATS check.
2. Application tracker (`add`, `log`, `stale`, `stats`, `serve`).
3. Job board watching and company following, with all current connectors.
4. Notifications: console by default, Telegram opt-in.
5. Scheduling of the daily poll (LaunchAgent on macOS, cron line on Linux).
6. Two-way sync with a per-user tracker artifact.

## Architecture

### Plugin repo layout

```
.claude-plugin/
  plugin.json
  marketplace.json
skills/
  init/SKILL.md
  tailor-cv/SKILL.md
  log-application/SKILL.md
  follow-company/SKILL.md
  build-connector/SKILL.md
  sync-tracker/SKILL.md
jobkit/                 Python package, stdlib only
  cli.py                `jobkit <command>` entry point
  config.py             loads jobkit.toml (tomllib on 3.11+, minimal parser below)
  cv.py                 load and validate cv.yaml, render, ATS check
  yamlmini.py           small YAML subset loader (see "YAML without dependencies")
  tracker.py            applications.md (today's jobtrack.py core)
  boards.py             connectors (today's jobtrack_boards.py)
  watch.py              watchlist, poll, follow, notify (today's jobtrack_watch.py)
  web.py                local UI (today's jobtrack_web.py)
  sync.py               tracker artifact sync (today's jobtrack_sync.py)
  doctor.py             environment checks
  schedule.py           LaunchAgent / cron installation
templates/
  cv.typ                ATS-safe single-column Typst template
samples/
  cv.yaml               fictional sample CV
tests/
  fixtures/
```

Skills call the engine as `python3 "${CLAUDE_PLUGIN_ROOT}/jobkit/cli.py" <cmd>`,
run from the user's data repo (the current working directory). The engine never
writes outside the data repo except for `schedule install` (LaunchAgent plist)
and reads of the configured Telegram token file.

### User data repo layout (created by `/jobkit:init`)

```
cv.yaml                         master CV
tailored/<company>.yaml         tailored variants (source of truth for each PDF)
tailored/<company>.pdf          rendered output
Applications/<YYYY-MM-DD> - <Company>/CV_<Name>.pdf
applications.md                 tracker, hand-editable (format unchanged)
watchlist.md                    watched boards and filters (format unchanged)
jobkit.toml                     per-user settings
CLAUDE.md                       short pointer to the jobkit skills plus personal rules
.gitignore                      state files (.jobkit-*.json, logs)
```

State files keep today's formats, renamed from `.jobtrack-*` to `.jobkit-*`. The
engine reads the old names if the new ones are missing, so migration needs no
conversion step.

### jobkit.toml

```toml
[user]
pdf_name = "CV_JaneDoe.pdf"     # filename inside Applications/<date> - <Company>/

[voice]
rules = [
  "No em dashes.",
  "Plain voice, no buzzwords.",
]

[notify]
channel = "console"              # "console" or "telegram"
telegram_env = "~/.claude/channels/telegram/.env"   # only read when channel = telegram

[tracker]
artifact_url = ""                # set by /jobkit:sync-tracker on first publish
```

Default watch filters stay in `watchlist.md` as today (`- include:` / `- exclude:`
/ `- location:` bullets), scaffolded empty by `init`.

### cv.yaml schema

```yaml
contact:
  name: Jane Doe
  phone: "+351 900 000 000"
  email: jane@example.com
  location: Porto, Portugal
  links:
    - {label: linkedin.com/in/janedoe, url: https://linkedin.com/in/janedoe}
profile: >
  One paragraph.
experience:
  - company: Example Corp
    location: Portugal
    title: Head of Strategy
    dates: 2021 - Present
    bullets:
      - {label: Team Leadership, text: Leads a team of 7 ...}
education:
  - school: University of Porto
    location: Porto
    degree: MSc Management
    dates: 2013 - 2015
    bullets: []
skills:
  Strategy: [Corporate strategy, M&A, PMO]
  Tools: [Power BI, n8n]
sections:                         # optional extra sections, rendered in order
  - title: Languages
    items: [Portuguese (native), English (fluent)]
```

Rules:

- `contact.name`, `profile`, at least one `experience` entry, `education` and
  `skills` are required. Validation errors name the field path
  (`experience[1].bullets[0].text: missing`).
- Text is plain Unicode. The template escapes Typst markup characters, so users
  never write markup. `label` renders bold, followed by a colon.
- Section headings are fixed to the ATS-standard names: "Professional Profile",
  "Professional Experience", "Education", "Skills".

### YAML without dependencies

The engine stays stdlib-only, so `yamlmini.py` parses the subset `cv.yaml` uses:
block mappings and sequences, flow mappings and sequences of scalars (`{a: b}`,
`[a, b]`), quoted and plain scalars, `>` and `|` block scalars, and comments.
Anything outside the subset is a clear error with a line number, not a silent
misparse. Claude writes tailored files in the same subset, and the
`tailor-cv` skill says so.

### Rendering and ATS check

- `jobkit render <file.yaml> [-o out.pdf]`: validates, passes the data to
  `templates/cv.typ` as JSON via `typst compile --input data=<path>`, writes the PDF.
- The template follows the existing ATS rules: single column, no tables or grids
  for entries, job entries as two plain lines (bold title, then
  `Company, Location | Dates`), nothing in headers or footers, standard headings.
- `jobkit ats-check <file.pdf> --source <file.yaml>`: runs `pdftotext` without
  `-layout` and checks that every section heading, every experience title, company
  and dates line, and the first words of every bullet appear in the extracted text
  in source order, with no duplicated fragments. Non-zero exit with a
  readable report on failure.

## Skills

Each SKILL.md carries the rules that live in this repo's CLAUDE.md today.

- **init**: scaffolds the data repo in the current directory (refuses to overwrite
  existing files), copies the sample `cv.yaml`, runs `jobkit doctor`, and offers to
  convert an existing CV (PDF, DOCX or LaTeX the user points at) into `cv.yaml`.
- **tailor-cv** `<posting url>`: fetch the posting; extract the job title, hard
  skills, tools and certifications as verbatim ATS keywords; write
  `tailored/<company>.yaml` by rewording `cv.yaml` only (no new accomplishments,
  no claim inflation), following `[voice]` rules; `render`; `ats-check` (stop and
  fix on failure); copy the PDF into `Applications/<date> - <Company>/`; `jobkit add`
  with the posting link; if the board has no connector, run build-connector; if a
  tracker artifact is configured, run sync-tracker.
- **log-application**: wraps `jobkit log` and status changes.
- **follow-company**: wraps `jobkit watch add` / `follow-postings`.
- **build-connector**: today's "Job board connectors" procedure. Probe for the
  most structured source; pure `parse_*` with a real response sample in tests;
  never get past bot protection, CAPTCHA, a login wall, or terms that forbid
  automation (add to `BLOCKED_HOSTS` and tell the user). With push access to
  jobkit, the connector goes into the plugin via a PR. Without it, it goes into
  `connectors_local.py` in the data repo (loaded at runtime if present) and the
  skill tells the user to open a PR upstream.
- **sync-tracker**: today's sync procedure. On first use it publishes the user's
  own tracker artifact and stores its URL in `jobkit.toml`.

## Other commands

- `jobkit doctor`: checks python version, `typst`, `pdftotext`, config validity,
  data repo layout; prints the `brew install` line for anything missing.
- `jobkit schedule install|uninstall`: macOS writes and loads the LaunchAgent for a
  daily `watch poll`; Linux prints a crontab line.
- All existing `jobtrack` commands keep their names and flags under `jobkit`.

## Error handling

- Missing binaries: commands that need them fail with the doctor hint.
- Invalid `cv.yaml`: field-path errors; no PDF is written.
- ATS check failure: the tailor-cv skill must fix the source and re-render, never
  ship the PDF.
- Telegram misconfigured: poll still records results and prints them; the error
  is reported once (existing behaviour).
- Board fetch errors: per-source health in `watch status` (existing behaviour).

## Testing

- Move today's test suite with the code; all of it must pass after the split.
- New tests: `yamlmini` (subset coverage and error lines), `cv.yaml` validation,
  config loading, `init` scaffolding into a temp dir, render plus a golden
  `pdftotext` output for `samples/cv.yaml` (skipped with a message when `typst`
  or `pdftotext` is missing), ATS checker pass and fail cases, `connectors_local.py`
  loading, `schedule` file generation.

## Rollout

1. Build `diogonmoura/jobkit`.
2. Move the author's own data repo onto it and use it for real applications.
3. Users install with `/plugin marketplace add diogonmoura/jobkit` then
   `/plugin install jobkit`.

## Out of scope

- Hosted MCP server, web sign-up, multi-user storage (Option B).
- LaTeX rendering.
- Windows support.
