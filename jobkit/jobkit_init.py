"""Create a user's data repo, and file finished CVs into Applications/."""
from __future__ import annotations

import datetime
import os
import re
import shutil
import unicodedata
from typing import List, Tuple

import cvrender

APPLICATIONS_MD = """# Applications

Source of truth for job applications. Hand-editable.

Status is one of: `applied`, `ack`, `screening`, `interview`, `offer`,
`rejected`, `withdrawn`, `ghosted`.

| Company | Role | Applied | CV | Status | Last contact | Posting |
|---|---|---|---|---|---|---|

## Events
"""

WATCHLIST_MD = """# Watchlist

Companies to monitor for new openings. Managed with `/jobkit:follow-company`
or the tracker UI (`jobkit serve`), and hand-editable.

**Default filters** — apply to every company unless its Keywords/Location columns override them.
- include:
- exclude:
- location:

| Company | Source | Keywords | Location | Added |
|---|---|---|---|---|
"""

JOBKIT_TOML = """[user]
pdf_name = "{pdf_name}"

[voice]
# Writing rules the tailor-cv skill follows, one string each.
rules = [
  "Plain, direct voice. No buzzwords.",
]

[notify]
channel = "console"    # or "telegram"
telegram_env = "~/.claude/channels/telegram/.env"

[tracker]
artifact_url = ""      # set by /jobkit:sync-tracker
"""

CLAUDE_MD = """# Job search data repo (jobkit)

This folder holds my CV and job search data. Use the jobkit skills:

- `/jobkit:tailor-cv <posting url>` to adapt `cv.md` to a posting, render, ATS-check, file and log it
- `/jobkit:log-application` to record replies, interviews and status changes
- `/jobkit:follow-company` to watch a company's job board
- `/jobkit:build-connector` when a board is not supported yet
- `/jobkit:sync-tracker` to sync with the tracker artifact

Engine commands run as `python3 ~/.jobkit/launcher.py <command>` from this folder.

## My rules

Add personal rules here (writing style, roles to avoid, ...).
"""

GITIGNORE = """.DS_Store
__pycache__/
.jobkit-*.json
.jobkit-*.json.*
.jobkit-watch.log
"""


def pdf_name_for(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "CV_{}.pdf".format(re.sub(r"[^A-Za-z0-9]", "", ascii_name) or "Me")


def scaffold(target: str, name: str) -> List[Tuple[str, str]]:
    files = [
        ("cv.md", None),
        ("jobkit.toml", JOBKIT_TOML.format(pdf_name=pdf_name_for(name))),
        ("applications.md", APPLICATIONS_MD),
        ("watchlist.md", WATCHLIST_MD),
        ("CLAUDE.md", CLAUDE_MD),
        (".gitignore", GITIGNORE),
        ("tailored/.gitkeep", ""),
        ("Applications/.gitkeep", ""),
    ]
    result: List[Tuple[str, str]] = []
    for relative, content in files:
        path = os.path.join(target, relative)
        if os.path.exists(path):
            result.append((relative, "kept"))
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if content is None:
            shutil.copy(os.path.join(cvrender.PLUGIN_ROOT, "samples", "cv.md"), path)
        else:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
        result.append((relative, "created"))
    return result


def file_cv(pdf: str, company: str, root: str, pdf_name: str, when: datetime.date) -> str:
    if not company.strip() or re.search(r"[/\\]|\.\.", company):
        raise ValueError("company must be a plain name, not {!r}".format(company))
    folder = os.path.join(root, "Applications", "{} - {}".format(when.isoformat(), company.strip()))
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, pdf_name)
    shutil.copy(pdf, dest)
    return dest
