"""Job application tracker: parse, analyse and update applications.md."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import statistics
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import jobkit_paths

STATUSES = (
    "applied",
    "ack",
    "screening",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "ghosted",
)
NEVER_STALE = frozenset({"offer", "rejected", "withdrawn", "ghosted"})
NO_RESPONSE = frozenset({"applied", "ghosted"})

APPLIED_STALE_DAYS = 14
ACTIVE_STALE_DAYS = 10
SMALL_SAMPLE_THRESHOLD = 30

COLUMNS = ("Company", "Role", "Applied", "CV", "Status", "Last contact", "Posting")
(
    COL_COMPANY,
    COL_ROLE,
    COL_APPLIED,
    COL_CV,
    COL_STATUS,
    COL_LAST_CONTACT,
    COL_POSTING,
) = range(7)

EVENTS_HEADING = "## Events"
HEADING_SEPARATOR = " — "
PLACEHOLDER = "—"

# The literal in the table's header row. Both the parser and the row lookup skip
# any row whose first cell is this, so a company actually named "Company" would
# be written and then silently ignored by every reader. Defined once so the two
# call sites and the writers' rejection of it can never drift apart.
HEADER_COMPANY = COLUMNS[COL_COMPANY]

_SEPARATOR_CELL = re.compile(r":?-+:?")
_EVENT_BULLET = re.compile(r"-\s+(\d{4}-\d{2}-\d{2})\s+(.*)")
_HORIZONTAL_RULE = re.compile(r"-+$")


class ParseError(Exception):
    """Raised when a log file cannot be read as a valid log."""

    def __init__(self, line_no: int, message: str, filename: str = "applications.md") -> None:
        self.line_no = line_no
        self.message = message
        self.filename = filename
        super().__init__("{} line {}: {}".format(filename, line_no, message))


@dataclass
class Event:
    date: datetime.date
    text: str


@dataclass
class Application:
    company: str
    role: str
    applied: datetime.date
    cv: str
    status: str
    last_contact: datetime.date
    posting: str
    events: List[Event] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.company, self.role)

    @property
    def label(self) -> str:
        return "{}{}{}".format(self.company, HEADING_SEPARATOR, self.role)


def split_row(line: str) -> Optional[List[str]]:
    """Split a markdown table row into stripped cells, or None if not a row."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_separator(cells: List[str]) -> bool:
    return all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def _parse_date(value: str, line_no: int, field_name: str, filename: str = "applications.md") -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ParseError(
            line_no, "{} must be YYYY-MM-DD, got {!r}".format(field_name, value), filename=filename
        ) from None


def _parse_row(cells: List[str], line_no: int) -> Application:
    if not cells[COL_COMPANY]:
        raise ParseError(line_no, "Company is empty")
    if not cells[COL_ROLE]:
        raise ParseError(line_no, "Role is empty")
    status = cells[COL_STATUS]
    if status not in STATUSES:
        raise ParseError(
            line_no,
            "unknown status {!r}; expected one of {}".format(status, ", ".join(STATUSES)),
        )
    return Application(
        company=cells[COL_COMPANY],
        role=cells[COL_ROLE],
        applied=_parse_date(cells[COL_APPLIED], line_no, "Applied"),
        cv=cells[COL_CV],
        status=status,
        last_contact=_parse_date(cells[COL_LAST_CONTACT], line_no, "Last contact"),
        posting=cells[COL_POSTING],
    )


def _split_heading(heading: str, line_no: int) -> Tuple[str, str]:
    if HEADING_SEPARATOR not in heading:
        raise ParseError(
            line_no,
            "events heading must be '### Company{}Role'".format(HEADING_SEPARATOR),
        )
    company, role = heading.split(HEADING_SEPARATOR, 1)
    return (company.strip(), role.strip())


def parse_log(text: str) -> List[Application]:
    """Parse applications.md text into Applications, in table order."""
    apps: Dict[Tuple[str, str], Application] = {}
    order: List[Tuple[str, str]] = []
    in_events = False
    current: Optional[Application] = None

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()

        if stripped.startswith(EVENTS_HEADING):
            in_events = True
            continue

        if not in_events:
            cells = split_row(line)
            if cells is None:
                continue
            if cells[COL_COMPANY] == HEADER_COMPANY:
                continue
            if _is_separator(cells):
                continue
            if len(cells) != len(COLUMNS):
                raise ParseError(
                    line_no,
                    "expected {} columns, found {}".format(len(COLUMNS), len(cells)),
                )
            app = _parse_row(cells, line_no)
            if app.key in apps:
                raise ParseError(line_no, "duplicate entry for {}".format(app.label))
            apps[app.key] = app
            order.append(app.key)
            continue

        if stripped.startswith("### "):
            key = _split_heading(stripped[4:].strip(), line_no)
            if key not in apps:
                raise ParseError(
                    line_no,
                    "events heading has no matching table row: {}{}{}".format(
                        key[0], HEADING_SEPARATOR, key[1]
                    ),
                )
            current = apps[key]
        elif stripped.startswith("-"):
            if _HORIZONTAL_RULE.fullmatch(stripped):
                continue
            match = _EVENT_BULLET.fullmatch(stripped)
            if match is None:
                raise ParseError(line_no, "event must be '- YYYY-MM-DD text'")
            if current is None:
                raise ParseError(line_no, "event bullet before any '### Company' heading")
            current.events.append(
                Event(
                    date=_parse_date(match.group(1), line_no, "event date"),
                    text=match.group(2).strip(),
                )
            )

    return [apps[key] for key in order]


@dataclass
class StaleItem:
    application: Application
    days_silent: int
    reason: str


def find_stale(apps: List[Application], today: datetime.date) -> List[StaleItem]:
    """Applications needing attention, most overdue first."""
    stale: List[StaleItem] = []
    for app in apps:
        if app.status in NEVER_STALE:
            continue
        days = (today - app.last_contact).days
        if app.status == "applied":
            if days > APPLIED_STALE_DAYS:
                stale.append(
                    StaleItem(
                        app,
                        days,
                        "no response in {} days — follow up or mark ghosted".format(days),
                    )
                )
        elif days > ACTIVE_STALE_DAYS:
            stale.append(
                StaleItem(
                    app,
                    days,
                    "active process gone quiet for {} days — chase".format(days),
                )
            )
    stale.sort(key=lambda item: (-item.days_silent, item.application.company))
    return stale


@dataclass
class Stats:
    total: int
    by_status: Dict[str, int]
    responded: int
    response_rate: float
    by_cv: Dict[str, Tuple[int, int]]
    median_days_to_first_response: Optional[float]
    median_sample_size: int
    small_sample: bool


def _has_responded(app: Application) -> bool:
    return app.status not in NO_RESPONSE


def _days_to_first_response(app: Application) -> Optional[int]:
    """Days from applying to the first event, for applications that replied.

    Gated on _has_responded so this can never contradict the response rate:
    events are also how the user logs their OWN actions ("sent follow-up"), so
    without the gate a still-'applied' row would report a time-to-response
    while the same stats block reported a 0% response rate.
    """
    if not _has_responded(app):
        return None
    replies = [event.date for event in app.events if event.date > app.applied]
    if not replies:
        return None
    return (min(replies) - app.applied).days


def compute_stats(apps: List[Application]) -> Stats:
    """Aggregate counts, response rates and time-to-response."""
    total = len(apps)
    by_status = {status: 0 for status in STATUSES}
    by_cv: Dict[str, Tuple[int, int]] = {}
    deltas: List[int] = []

    for app in apps:
        by_status[app.status] += 1
        responded, seen = by_cv.get(app.cv, (0, 0))
        by_cv[app.cv] = (responded + (1 if _has_responded(app) else 0), seen + 1)
        delta = _days_to_first_response(app)
        if delta is not None:
            deltas.append(delta)

    responded_total = sum(1 for app in apps if _has_responded(app))
    return Stats(
        total=total,
        by_status=by_status,
        responded=responded_total,
        response_rate=(responded_total / total) if total else 0.0,
        by_cv=by_cv,
        median_days_to_first_response=statistics.median(deltas) if deltas else None,
        median_sample_size=len(deltas),
        small_sample=total < SMALL_SAMPLE_THRESHOLD,
    )


class NotFound(Exception):
    """Raised when no table row matches the given company and role."""


class Duplicate(Exception):
    """Raised when adding an application that is already logged."""


# Every writer validates its inputs BEFORE touching the text, so a rejected
# call always leaves the log exactly as it was. Each of these characters or
# shapes produces a file that writes successfully and then fails to parse on
# the very next read, which bricks every later command.
_LINE_BREAKS = (("\n", "\\n"), ("\r", "\\r"))


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError("{} must not be empty".format(field_name))


def _reject_line_breaks(value: str, field_name: str) -> None:
    for char, shown in _LINE_BREAKS:
        if char in value:
            raise ValueError(
                "{} must not contain '{}' (splits the entry across lines): {!r}".format(
                    field_name, shown, value
                )
            )


def _reject_pipe(value: str, field_name: str) -> None:
    if "|" in value:
        raise ValueError(
            "{} must not contain '|' (breaks table columns): {!r}".format(field_name, value)
        )


def _validate_field(value: str, field_name: str) -> None:
    """Validate a company or role: it appears in the row AND the events heading."""
    _require_text(value, field_name)
    _reject_line_breaks(value, field_name)
    _reject_pipe(value, field_name)
    if HEADING_SEPARATOR in value:
        raise ValueError(
            "{} must not contain {!r} (breaks the events heading): {!r}".format(
                field_name, HEADING_SEPARATOR, value
            )
        )


def _validate_company(value: str) -> None:
    """A company is a field, plus it must not collide with the header literal."""
    _validate_field(value, "company")
    if value.strip() == HEADER_COMPANY:
        raise ValueError(
            "company must not be {!r}: every reader treats that row as the "
            "table header and skips it".format(HEADER_COMPANY)
        )


def _validate_cell(value: str, field_name: str) -> None:
    """Validate a plain table cell (cv, posting): may be blank, must stay on one line."""
    _reject_line_breaks(value, field_name)
    _reject_pipe(value, field_name)


def _validate_note(value: str) -> None:
    """Validate an event note: it becomes the tail of a '- YYYY-MM-DD text' bullet."""
    _require_text(value, "event note")
    _reject_line_breaks(value, "event note")


def _render_row(cells: List[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _row_index(lines: List[str], company: str, role: str) -> Optional[int]:
    for index, line in enumerate(lines):
        cells = split_row(line)
        if cells is None or len(cells) != len(COLUMNS):
            continue
        if cells[COL_COMPANY] == HEADER_COMPANY or _is_separator(cells):
            continue
        if cells[COL_COMPANY] == company and cells[COL_ROLE] == role:
            return index
    return None


def _rejoin(lines: List[str], original: str) -> str:
    text = "\n".join(lines)
    return text + "\n" if original.endswith("\n") else text


def _set_cell(text: str, company: str, role: str, column: int, value: str) -> str:
    lines = text.splitlines()
    index = _row_index(lines, company, role)
    if index is None:
        raise NotFound(
            "no application for {}{}{}".format(company, HEADING_SEPARATOR, role)
        )
    cells = split_row(lines[index])
    assert cells is not None  # guaranteed by _row_index
    cells[column] = value
    lines[index] = _render_row(cells)
    return _rejoin(lines, text)


def set_status(text: str, company: str, role: str, status: str) -> str:
    """Set a row's Status. Raises ValueError for an unknown status.

    company/role are also validated (not just looked up): a malformed identifier
    can never match a legitimately-written row, so without this check the call
    would fail with a confusing NotFound instead of naming the actual problem.
    """
    _validate_company(company)
    _validate_field(role, "role")
    if status not in STATUSES:
        raise ValueError(
            "unknown status {!r}; expected one of {}".format(status, ", ".join(STATUSES))
        )
    return _set_cell(text, company, role, COL_STATUS, status)


def append_application(text: str, app: Application) -> str:
    """Append a new table row directly after the last existing row."""
    _validate_company(app.company)
    _validate_field(app.role, "role")
    _validate_cell(app.cv, "cv")
    _validate_cell(app.posting, "posting")
    if app.status not in STATUSES:
        raise ValueError(
            "unknown status {!r}; expected one of {}".format(app.status, ", ".join(STATUSES))
        )
    lines = text.splitlines()
    if _row_index(lines, app.company, app.role) is not None:
        raise Duplicate("{} is already logged".format(app.label))

    last_row = None
    for index, line in enumerate(lines):
        if line.strip().startswith(EVENTS_HEADING):
            break
        cells = split_row(line)
        if cells is not None and len(cells) == len(COLUMNS):
            last_row = index
    if last_row is None:
        raise NotFound("applications.md has no table to append to")

    row = _render_row(
        [
            app.company,
            app.role,
            app.applied.isoformat(),
            app.cv or PLACEHOLDER,
            app.status,
            app.last_contact.isoformat(),
            app.posting or PLACEHOLDER,
        ]
    )
    lines.insert(last_row + 1, row)
    return _rejoin(lines, text)


def _heading_for(company: str, role: str) -> str:
    return "### {}{}{}".format(company, HEADING_SEPARATOR, role)


def append_event(
    text: str, company: str, role: str, date: datetime.date, note: str
) -> str:
    """Append a dated event and move Last contact forward. Status is left alone.

    Last contact becomes max(existing, event date), not simply the event date:
    backfilling an older event must not make the row look staler than it is.
    (The spec says "updates Last contact to the event date" — this is the
    deliberate reading of what that is for, since a row whose last contact
    moved backwards gets wrongly flagged as overdue.) When the event is older,
    the row is left byte-identical rather than rewritten with the same value.
    """
    _validate_field(company, "company")
    _validate_field(role, "role")
    _validate_note(note)

    lines = text.splitlines()
    index = _row_index(lines, company, role)
    if index is None:
        raise NotFound(
            "no application for {}{}{}".format(company, HEADING_SEPARATOR, role)
        )
    cells = split_row(lines[index])
    assert cells is not None  # guaranteed by _row_index
    try:
        current = datetime.date.fromisoformat(cells[COL_LAST_CONTACT])
    except ValueError:
        current = None  # already unparseable; writing a real date can only help
    if current is None or date > current:
        text = _set_cell(text, company, role, COL_LAST_CONTACT, date.isoformat())

    lines = text.splitlines()
    bullet = "- {} {}".format(date.isoformat(), note)

    if not any(line.strip().startswith(EVENTS_HEADING) for line in lines):
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend(["", EVENTS_HEADING])

    heading = _heading_for(company, role)
    try:
        start = lines.index(heading)
    except ValueError:
        while lines and not lines[-1].strip():
            lines.pop()
        # No trailing "" here: _rejoin already appends the single trailing
        # newline when the original text had one. An extra "" element would
        # make "\n".join(lines) end in \n on its own, and _rejoin would then
        # add a second one (or add one where the original had none).
        lines.extend(["", heading, bullet])
        return _rejoin(lines, text)

    end = start + 1
    while end < len(lines) and lines[end].strip().startswith("- "):
        end += 1

    insert_at = end
    for index in range(start + 1, end):
        match = _EVENT_BULLET.fullmatch(lines[index].strip())
        if match and datetime.date.fromisoformat(match.group(1)) > date:
            insert_at = index
            break
    lines.insert(insert_at, bullet)
    return _rejoin(lines, text)


def read_log(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write_log_atomic(path: str, text: str) -> None:
    """Write via a same-directory temp file and rename, so writes can't truncate."""
    directory = os.path.dirname(os.path.abspath(path))
    handle, temp_path = tempfile.mkstemp(dir=directory, prefix=".jobtrack-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp_path, path)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def default_log_path() -> str:
    return jobkit_paths.data_file("applications.md")


def stale_to_json(items: List[StaleItem]) -> List[dict]:
    return [
        {
            "company": item.application.company,
            "role": item.application.role,
            "status": item.application.status,
            "days_silent": item.days_silent,
            "reason": item.reason,
            "posting": item.application.posting,
        }
        for item in items
    ]


def stats_to_json(stats: Stats) -> dict:
    return {
        "total": stats.total,
        "by_status": stats.by_status,
        "responded": stats.responded,
        "response_rate": round(stats.response_rate, 4),
        "by_cv": {cv: {"responded": r, "total": t} for cv, (r, t) in stats.by_cv.items()},
        "median_days_to_first_response": stats.median_days_to_first_response,
        "median_sample_size": stats.median_sample_size,
        "small_sample": stats.small_sample,
    }


def format_stale(items: List[StaleItem]) -> str:
    if not items:
        return "Nothing overdue."
    lines = ["{} application(s) need attention:".format(len(items)), ""]
    for item in items:
        lines.append("  {:<40} {}".format(item.application.label, item.reason))
    return "\n".join(lines)


def format_stats(stats: Stats) -> str:
    lines = ["{} application(s) logged.".format(stats.total), ""]
    for status in STATUSES:
        if stats.by_status[status]:
            lines.append("  {:<12} {}".format(status, stats.by_status[status]))
    lines.append("")
    lines.append(
        "Response rate: {:.0%}  ({}/{})".format(
            stats.response_rate, stats.responded, stats.total
        )
    )
    if stats.by_cv:
        lines.append("")
        lines.append("By CV variant:")
        for cv in sorted(stats.by_cv):
            responded, seen = stats.by_cv[cv]
            lines.append("  {:<24} {}/{}".format(cv, responded, seen))
    lines.append("")
    if stats.median_days_to_first_response is None:
        lines.append("Median days to first response: no responses logged yet")
    else:
        lines.append(
            "Median days to first response: {:g}  (from {} application(s))".format(
                stats.median_days_to_first_response, stats.median_sample_size
            )
        )
    if stats.small_sample:
        lines.append("")
        lines.append(
            "Note: sample is too small to draw conclusions from "
            "(fewer than {} applications).".format(SMALL_SAMPLE_THRESHOLD)
        )
    return "\n".join(lines)


def _load(path: str) -> str:
    try:
        return read_log(path)
    except FileNotFoundError:
        raise SystemExit(
            "{} not initialised — no log at {}".format(os.path.basename(path), path)
        )


def _state_in(folder: str, name: str) -> str:
    """A jobkit state file in `folder`, keeping its pre-jobkit name if only that exists."""
    new = os.path.join(folder, name)
    legacy = os.path.join(folder, jobkit_paths.LEGACY_NAMES[name])
    return legacy if os.path.exists(legacy) and not os.path.exists(new) else new


def _follow_path(watchlist_path: str) -> str:
    return _state_in(os.path.dirname(os.path.abspath(watchlist_path)), ".jobkit-follow.json")


def _follow_new_application(log_path: str, app: Application, today: datetime.date) -> None:
    """Follow the company behind a new application's posting link.

    The watchlist and its state live next to applications.md. An application
    is already saved by the time this runs, so nothing here may fail the add:
    every problem is reported as a note instead.
    """
    url = (app.posting or "").strip()
    root = os.path.dirname(os.path.abspath(log_path))
    watchlist_path = os.path.join(root, "watchlist.md")
    if not url.startswith("http") or not os.path.exists(watchlist_path):
        return
    import jobtrack_watch

    try:
        result = jobtrack_watch.follow_posting(
            app.company, url, watchlist_path, _state_in(root, ".jobkit-watch-state.json"), today
        )
        follow_path = _follow_path(watchlist_path)
        follows = jobtrack_watch.load_follows(follow_path)
        jobtrack_watch.record_follow(follows, url, result, today)
        jobtrack_watch.save_follows(follow_path, follows)
    except (ParseError, NotFound, Duplicate, ValueError, OSError) as error:
        print("Note: could not follow {} for new roles ({}).".format(app.company, error), file=sys.stderr)
        return
    print(result.describe())
    if result.warning:
        print("Warning: " + result.warning, file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    # --file lives on a shared parent parser so it is accepted AFTER the
    # subcommand (`jobtrack stale --file X`). Do not also put it on the
    # top-level parser: argparse would let the subparser's default silently
    # overwrite a value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", default=default_log_path(), help="path to applications.md")

    parser = argparse.ArgumentParser(prog="jobkit", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    stale_cmd = subparsers.add_parser(
        "stale", parents=[common], help="applications needing follow-up"
    )
    stale_cmd.add_argument("--json", action="store_true", dest="as_json")

    stats_cmd = subparsers.add_parser(
        "stats", parents=[common], help="response rates and timings"
    )
    stats_cmd.add_argument("--json", action="store_true", dest="as_json")

    add = subparsers.add_parser("add", parents=[common], help="append an application")
    add.add_argument("--company", required=True)
    add.add_argument("--role", required=True)
    add.add_argument("--applied", default=None, help="YYYY-MM-DD, defaults to today")
    add.add_argument("--cv", default=PLACEHOLDER)
    add.add_argument("--posting", default=PLACEHOLDER)
    add.add_argument(
        "--no-follow", action="store_true", dest="no_follow",
        help="do not add the posting's company to the watchlist",
    )

    event = subparsers.add_parser("log", parents=[common], help="append a dated event")
    event.add_argument("--company", required=True)
    event.add_argument("--role", required=True)
    event.add_argument("--text", required=True)
    event.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today")

    serve_cmd = subparsers.add_parser(
        "serve", parents=[common], help="start the local web UI"
    )
    serve_cmd.add_argument("--port", type=int, default=8765)

    watch = subparsers.add_parser("watch", parents=[common], help="watch company job boards")
    watch.add_argument("--watchlist", default=None, help="path to watchlist.md")
    watch.add_argument("--state", default=None, help="path to the watch state file")
    watch_actions = watch.add_subparsers(dest="watch_action", required=True)

    w_add = watch_actions.add_parser("add", help="add a company by careers URL")
    w_add.add_argument("--url", required=True)
    w_add.add_argument("--company", default=None, help="defaults to the resolved slug")
    w_add.add_argument("--keywords", default=None, help="comma-separated override")

    w_remove = watch_actions.add_parser("remove", help="remove a company")
    w_remove.add_argument("--company", required=True)

    watch_actions.add_parser("list", help="show the watchlist")
    watch_actions.add_parser("status", help="per-source health")

    w_follow = watch_actions.add_parser(
        "follow-postings", help="follow every applied-to company whose posting link is new"
    )
    w_follow.add_argument(
        "--retry", action="store_true", help="re-try postings whose board was unsupported"
    )

    watch_actions.add_parser(
        "push-postings", help="send postings from boards the web app can't read to the web app"
    )

    w_poll = watch_actions.add_parser("poll", help="fetch and notify on new roles")
    w_poll.add_argument("--dry-run", action="store_true", dest="dry_run")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    today = datetime.date.today()

    try:
        if args.command == "stale":
            items = find_stale(parse_log(_load(args.file)), today)
            print(json.dumps(stale_to_json(items), indent=2) if args.as_json else format_stale(items))
            return 0

        if args.command == "stats":
            stats = compute_stats(parse_log(_load(args.file)))
            print(json.dumps(stats_to_json(stats), indent=2) if args.as_json else format_stats(stats))
            return 0

        if args.command == "add":
            applied = datetime.date.fromisoformat(args.applied) if args.applied else today
            app = Application(
                company=args.company,
                role=args.role,
                applied=applied,
                cv=args.cv,
                status="applied",
                last_contact=applied,
                posting=args.posting,
            )
            text = append_application(_load(args.file), app)
            write_log_atomic(args.file, text)
            print("Added {}".format(app.label))
            if not args.no_follow:
                _follow_new_application(args.file, app, today)
            return 0

        if args.command == "log":
            when = datetime.date.fromisoformat(args.date) if args.date else today
            text = append_event(_load(args.file), args.company, args.role, when, args.text)
            write_log_atomic(args.file, text)
            print("Logged {} for {}{}{}".format(when, args.company, HEADING_SEPARATOR, args.role))
            return 0

        if args.command == "serve":
            import jobtrack_web

            jobtrack_web.serve(args.file, args.port)
            return 0

        if args.command == "watch":
            import jobtrack_watch

            wl_path = args.watchlist or jobtrack_watch.default_watchlist_path()
            st_path = args.state or jobtrack_watch.default_state_path()

            try:
                if args.watch_action == "add":
                    source = jobtrack_watch.resolve_url(args.url)
                    company = args.company or source.slug
                    keywords = (
                        [k.strip() for k in args.keywords.split(",") if k.strip()]
                        if args.keywords
                        else None
                    )
                    # Validate (including the duplicate-company check inside
                    # add_watch) BEFORE the network fetch below -- a duplicate
                    # add is then rejected without ever touching the network.
                    text = jobtrack_watch.add_watch(
                        _load(wl_path), company, source, keywords, today
                    )
                    postings = jobtrack_watch.fetch_postings(source)
                    write_log_atomic(wl_path, text)
                    # Once the line above succeeds the company IS on the
                    # watchlist, so nothing after it may be reported as the add
                    # having failed. Letting a save_state OSError fall through
                    # to the outer handler printed an errno and exited 1 for an
                    # operation that had already succeeded -- the user then
                    # re-runs it and gets a spurious "already on the
                    # watchlist". Downgrade it to a warning naming the STATE
                    # file (not the watchlist, which was written fine). The
                    # data outcome self-heals: an entry with last_ok=None seeds
                    # silently on the next poll instead of notifying on every
                    # existing posting. Mirrors jobtrack_web._add_watch.
                    seeding_warning = None
                    try:
                        state = jobtrack_watch.load_state(st_path)
                        state[str(source)] = jobtrack_watch.SourceState(
                            seen_ids=sorted({p.id for p in postings}), last_ok=today.isoformat()
                        )
                        jobtrack_watch.save_state(st_path, state)
                    except OSError as error:
                        seeding_warning = (
                            "Warning: {} was added, but recording its poll history in "
                            "{} failed ({}); the next poll will seed it normally.".format(
                                company, st_path, error
                            )
                        )
                    if seeding_warning:
                        print("Watching {} via {}.".format(company, source))
                        print(seeding_warning, file=sys.stderr)
                    else:
                        print(
                            "Watching {} via {} — {} current posting(s) recorded as seen, "
                            "so only new ones will notify.".format(company, source, len(postings))
                        )
                    if not postings:
                        print(
                            "Warning: that board returned zero postings. The slug may be wrong.",
                            file=sys.stderr,
                        )
                    return 0

                if args.watch_action == "push-postings":
                    import jobkit_config
                    import jobkit_web

                    try:
                        client = jobkit_web.client_from_config(jobkit_config.load())
                        result = jobtrack_watch.push_postings(
                            jobtrack_watch.parse_watchlist(_load(wl_path)), client, client.owner_id()
                        )
                    except jobkit_web.WebError as error:
                        print("error: {}".format(error), file=sys.stderr)
                        return 1
                    print(json.dumps(result))
                    return 0

                if args.watch_action == "follow-postings":
                    results = jobtrack_watch.follow_postings(
                        parse_log(_load(args.file)), wl_path, st_path,
                        _follow_path(wl_path), today, retry=args.retry,
                    )
                    for result in results:
                        print(result.describe())
                        if result.warning:
                            print("Warning: " + result.warning, file=sys.stderr)
                    if not results:
                        print("No new posting links to follow.")
                    return 0

                if args.watch_action == "remove":
                    write_log_atomic(wl_path, jobtrack_watch.remove_watch(_load(wl_path), args.company))
                    print("Removed {}".format(args.company))
                    return 0

                if args.watch_action == "list":
                    print(_load(wl_path))
                    return 0

                if args.watch_action == "status":
                    print(
                        jobtrack_watch.format_status(
                            jobtrack_watch.parse_watchlist(
                                _load(wl_path), filename=os.path.basename(wl_path)
                            ),
                            jobtrack_watch.load_state(st_path),
                        )
                    )
                    return 0

                if args.watch_action == "poll":
                    results = jobtrack_watch.run_poll(
                        wl_path, st_path, today, notify_enabled=not args.dry_run
                    )
                    total = sum(len(r.new) for r in results)
                    failed = [r for r in results if r.error]
                    for result in results:
                        if result.new:
                            print(jobtrack_watch.format_notification(result))
                            print()
                    print("{} new role(s) across {} source(s).".format(total, len(results)))
                    for result in failed:
                        print("  FAILED {}: {}".format(result.company, result.error), file=sys.stderr)
                    return 0
            except (
                jobtrack_watch.ResolveError,
                jobtrack_watch.AdapterError,
                jobtrack_watch.NotifyError,
            ) as error:
                print(str(error), file=sys.stderr)
                return 1
    except (ParseError, NotFound, Duplicate, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except SystemExit as error:
        print(str(error), file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    # jobtrack_watch does `import jobtrack`. Without this, that import would
    # load tools/jobtrack.py a SECOND time under the module name "jobtrack" --
    # a distinct module object from __main__, with its own distinct ParseError/
    # NotFound/Duplicate classes. main()'s `except (ParseError, NotFound,
    # Duplicate, ...)` binds to __main__'s classes, so an exception raised by
    # jobtrack_watch as `jobtrack.NotFound(...)` (the OTHER instance) would
    # never match, and escape as a raw traceback instead of a clean stderr
    # message. Registering the running module under its importable name
    # BEFORE anything imports it means the later `import jobtrack` finds this
    # exact module object already in sys.modules and reuses it, so both sides
    # share one set of exception classes.
    sys.modules.setdefault("jobtrack", sys.modules["__main__"])
    sys.exit(main())
