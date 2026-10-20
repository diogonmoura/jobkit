"""Watch company job boards and notify on new matching roles."""

from __future__ import annotations

import datetime
import html.parser
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import jobkit_config
import jobkit_paths
import jobtrack

VENDORS = (
    "greenhouse", "lever", "lever-eu", "ashby", "smartrecruiters", "successfactors",
    "workday", "taleo", "teamtailor",
    # Connectors in jobtrack_boards.py (kept in step by a test):
    "oracle", "cornerstone", "join", "avature", "bendingspoons", "mantu", "itjobs",
)

WATCH_COLUMNS = ("Company", "Source", "Keywords", "Location", "Added")
WCOL_COMPANY, WCOL_SOURCE, WCOL_KEYWORDS, WCOL_LOCATION, WCOL_ADDED = range(5)
HEADER_COMPANY = WATCH_COLUMNS[WCOL_COMPANY]

DEFAULT_TIMEOUT = 20
PLACEHOLDER = jobtrack.PLACEHOLDER

def default_watchlist_path() -> str:
    return jobkit_paths.data_file("watchlist.md")


def default_state_path() -> str:
    return jobkit_paths.state_file(".jobkit-watch-state.json")


@dataclass
class Filters:
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    location: List[str] = field(default_factory=list)


@dataclass
class Source:
    vendor: str
    slug: str

    def __str__(self) -> str:
        return "{}:{}".format(self.vendor, self.slug)


@dataclass
class Watch:
    company: str
    source: Source
    keywords: Optional[List[str]]
    added: datetime.date
    location: Optional[List[str]] = None


@dataclass
class Watchlist:
    defaults: Filters
    watches: List[Watch]


def _split_terms(value: str) -> List[str]:
    return [
        term.strip()
        for term in value.split(",")
        if term.strip() and term.strip() != PLACEHOLDER
    ]


DEFAULT_WATCHLIST_NAME = "watchlist.md"


def _parse_source(value: str, line_no: int, filename: str = DEFAULT_WATCHLIST_NAME) -> Source:
    if ":" not in value:
        raise jobtrack.ParseError(
            line_no, "Source must be vendor:slug, got {!r}".format(value), filename=filename
        )
    vendor, slug = value.split(":", 1)
    vendor, slug = vendor.strip(), slug.strip()
    if vendor not in VENDORS:
        raise jobtrack.ParseError(
            line_no,
            "unknown vendor {!r}; supported: {}".format(vendor, ", ".join(VENDORS)),
            filename=filename,
        )
    if not slug:
        raise jobtrack.ParseError(line_no, "Source slug is empty", filename=filename)
    return Source(vendor=vendor, slug=slug)


def parse_watchlist(text: str, filename: str = DEFAULT_WATCHLIST_NAME) -> Watchlist:
    """Parse watchlist.md into defaults plus watched companies, in table order.

    `filename` only ever names the file in ParseError messages. It is threaded
    through from the caller (mirroring jobtrack._parse_date) so that a parse
    failure under `--watchlist bad.md` reads "bad.md line 12: ..." instead of
    blaming the default watchlist.md the user never touched.
    """
    defaults = Filters()
    watches: List[Watch] = []
    seen: Dict[str, int] = {}

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()

        if stripped.startswith("- include:"):
            defaults.include = _split_terms(stripped[len("- include:"):])
            continue
        if stripped.startswith("- exclude:"):
            defaults.exclude = _split_terms(stripped[len("- exclude:"):])
            continue
        if stripped.startswith("- location:"):
            defaults.location = _split_terms(stripped[len("- location:"):])
            continue

        cells = jobtrack.split_row(line)
        if cells is None:
            continue
        if cells[WCOL_COMPANY] == HEADER_COMPANY:
            continue
        if jobtrack._is_separator(cells):
            continue
        if len(cells) != len(WATCH_COLUMNS):
            raise jobtrack.ParseError(
                line_no,
                "expected {} columns, found {}".format(len(WATCH_COLUMNS), len(cells)),
                filename=filename,
            )

        company = cells[WCOL_COMPANY]
        if not company:
            raise jobtrack.ParseError(line_no, "Company is empty", filename=filename)
        if company in seen:
            raise jobtrack.ParseError(
                line_no, "duplicate entry for {}".format(company), filename=filename
            )
        seen[company] = line_no

        raw_keywords = cells[WCOL_KEYWORDS]
        raw_location = cells[WCOL_LOCATION]
        watches.append(
            Watch(
                company=company,
                source=_parse_source(cells[WCOL_SOURCE], line_no, filename=filename),
                keywords=None if raw_keywords in ("", PLACEHOLDER) else _split_terms(raw_keywords),
                location=None if raw_location in ("", PLACEHOLDER) else _split_terms(raw_location),
                added=jobtrack._parse_date(
                    cells[WCOL_ADDED], line_no, "Added", filename=filename
                ),
            )
        )

    return Watchlist(defaults=defaults, watches=watches)


class AdapterError(Exception):
    """A source could not be fetched or its payload could not be understood."""


@dataclass
class Posting:
    id: str
    title: str
    url: str
    location: str
    posted: Optional[str]


def _epoch_millis_to_iso(value) -> Optional[str]:
    try:
        return datetime.datetime.utcfromtimestamp(int(value) / 1000).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _as_dict(value) -> dict:
    """Coerce a possibly-wrong-typed value to a dict, for tolerant field access.

    Vendors occasionally return a record as None, or a field as the wrong
    type (a string where a nested object was expected). Rather than let a
    single malformed record raise and discard every other record on the
    same board, every parse_* below routes field access through this (and
    _as_list/_as_str) so a bad record degrades to empty fields instead.
    """
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _as_str(value) -> str:
    return value if isinstance(value, str) else ""


def _job_id(value) -> str:
    """Normalise a raw vendor id to a string, or "" when there is no usable id.

    NOT str(job.get("id", "")): dict.get returns the STORED value whenever the
    key is present, so an explicit `"id": null` yields None and the default
    never applies -- str(None) is the truthy string "None", which sails past
    every caller's no-id skip guard and becomes a phantom posting. Worse, two
    null-id records collapse onto the single seen id "None", so the second one
    is silently swallowed as already-seen. `"id": false` -> "False" the same
    way, which is why bools are rejected here too (bool is an int subclass, so
    it has to be checked before the numeric case).
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""  # dicts, lists, anything else: not an id we can key state by


def _require_container(payload, key: Optional[str], vendor: str) -> list:
    """Validate the top-level shape a vendor's payload must have, and return
    the list of raw job records to iterate.

    This is deliberately strict where _as_dict/_as_list/_as_str (above) are
    deliberately lenient: a wrong-typed *field* on an otherwise-recognisable
    record (a string location, a None record) should degrade to an empty
    value so one bad record doesn't cost the rest of the board its postings.
    But a wrong-shaped *container* -- the top-level payload isn't a dict at
    all, or the list of jobs is missing or isn't a list -- means we can't
    tell postings from silence. A source that's actually empty must still
    return [] without raising (health tracking depends on that distinction);
    anything else here is loud on purpose.

    key=None means the payload itself is expected to be the top-level list
    (Lever). Otherwise payload must be a dict containing `key` as a list.
    """
    if key is None:
        if not isinstance(payload, list):
            raise AdapterError(
                "{}: expected a top-level list, got {}".format(vendor, type(payload).__name__)
            )
        return payload

    if not isinstance(payload, dict):
        raise AdapterError(
            "{}: expected a dict with a {!r} list, got {}".format(vendor, key, type(payload).__name__)
        )
    if key not in payload:
        raise AdapterError("{}: expected a {!r} key, found none".format(vendor, key))
    jobs = payload[key]
    if not isinstance(jobs, list):
        raise AdapterError(
            "{}: expected {!r} to be a list, got {}".format(vendor, key, type(jobs).__name__)
        )
    return jobs


def parse_greenhouse(payload) -> List[Posting]:
    postings = []
    for job in _require_container(payload, "jobs", "greenhouse"):
        job = _as_dict(job)
        job_id = _job_id(job.get("id"))
        if not job_id:
            continue  # no usable id -- would pollute the seen-id set as a phantom
        location = _as_str(_as_dict(job.get("location")).get("name"))
        postings.append(
            Posting(
                id=job_id,
                title=_as_str(job.get("title")),
                url=_as_str(job.get("absolute_url")),
                location=location,
                posted=job.get("updated_at"),
            )
        )
    return postings


def parse_lever(payload) -> List[Posting]:
    postings = []
    for job in _require_container(payload, None, "lever"):
        job = _as_dict(job)
        job_id = _job_id(job.get("id"))
        if not job_id:
            continue
        categories = _as_dict(job.get("categories"))
        postings.append(
            Posting(
                id=job_id,
                title=_as_str(job.get("text")),
                url=_as_str(job.get("hostedUrl")),
                location=_as_str(categories.get("location")),
                posted=_epoch_millis_to_iso(job.get("createdAt")),
            )
        )
    return postings


def parse_ashby(payload) -> List[Posting]:
    postings = []
    for job in _require_container(payload, "jobs", "ashby"):
        job = _as_dict(job)
        job_id = _job_id(job.get("id"))
        if not job_id:
            continue
        postings.append(
            Posting(
                id=job_id,
                title=_as_str(job.get("title")),
                url=_as_str(job.get("jobUrl")),
                location=_as_str(job.get("location")),
                posted=job.get("publishedAt"),
            )
        )
    return postings


def parse_smartrecruiters(payload, slug: str) -> List[Posting]:
    postings = []
    for job in _require_container(payload, "content", "smartrecruiters"):
        job = _as_dict(job)
        job_id = _job_id(job.get("id"))
        if not job_id:
            continue
        location = _as_dict(job.get("location"))
        text = _as_str(location.get("fullLocation"))
        if not text:
            text = ", ".join(
                part
                for part in (_as_str(location.get("city")), _as_str(location.get("region")))
                if part
            )
        postings.append(
            Posting(
                id=job_id,
                title=_as_str(job.get("name")),
                url="https://jobs.smartrecruiters.com/{}/{}".format(slug, job_id),
                location=text,
                posted=job.get("releasedDate"),
            )
        )
    return postings


_parse_lever_func = lambda payload, slug: parse_lever(payload)


class _SuccessFactorsRowParser(html.parser.HTMLParser):
    """Parses one page of SAP SuccessFactors Career Site Builder search
    results into Postings.

    Tolerant of the platform's duplicated mobile/desktop markup for the same
    row (each field renders twice -- once inside a `visible-phone` block,
    once in a `hidden-phone` cell -- with identical text): keeps the first
    non-empty value seen per field per row, ignores the duplicate. Does NOT
    defend against an unrelated tag nesting inside a field's own span (e.g. a
    decorative icon span inside the location text) -- the real markup
    observed has no such case; if a future theme introduces one, the fix is
    to track field attribution by a matched-tag stack rather than tag-type
    alone.
    """

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.postings: List[Posting] = []
        self.saw_noresults = False
        self._in_row = False
        self._field_stack: List[str] = []
        self._row: Dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = (attrs_dict.get("class") or "").split()

        if attrs_dict.get("id") == "noresults-message":
            self.saw_noresults = True

        if tag == "tr" and "data-row" in classes:
            self._in_row = True
            self._row = {}
            self._field_stack = []
            return

        if not self._in_row:
            return

        if tag == "a" and "jobTitle-link" in classes:
            href = attrs_dict.get("href", "")
            if "id" not in self._row:
                match = re.search(r"/(\d+)/?$", href)
                if match:
                    self._row["id"] = match.group(1)
                    self._row["url"] = self.base_url + href
            self._field_stack.append("title")
            return

        if tag == "span":
            if "jobLocation" in classes:
                self._field_stack.append("location")
            elif "jobDate" in classes:
                self._field_stack.append("date")

    def handle_endtag(self, tag):
        if tag == "tr" and self._in_row:
            self._finish_row()
            self._in_row = False
            return
        if self._field_stack and tag in ("a", "span"):
            self._field_stack.pop()

    def handle_data(self, data):
        if not self._in_row or not self._field_stack:
            return
        field = self._field_stack[-1]
        text = data.strip()
        if not text:
            return
        if not self._row.get(field):
            self._row[field] = text

    def _finish_row(self):
        if "id" in self._row and "title" in self._row:
            self.postings.append(
                Posting(
                    id=self._row["id"],
                    title=self._row.get("title", ""),
                    url=self._row.get("url", ""),
                    location=self._row.get("location", ""),
                    posted=self._row.get("date"),
                )
            )


ADAPTERS = {
    "greenhouse": {
        "url": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        "parse": lambda payload, slug: parse_greenhouse(payload),
    },
    "lever": {
        "url": "https://api.lever.co/v0/postings/{slug}?mode=json",
        "parse": _parse_lever_func,
    },
    "lever-eu": {
        "url": "https://api.eu.lever.co/v0/postings/{slug}?mode=json",
        "parse": _parse_lever_func,
    },
    "ashby": {
        "url": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
        "parse": lambda payload, slug: parse_ashby(payload),
    },
    "smartrecruiters": {
        "url": "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
        "parse": lambda payload, slug: parse_smartrecruiters(payload, slug),
    },
    "successfactors": {
        "url": "https://{slug}/search/?q=",
        "parse": None,  # handled specially in fetch_postings -- HTML pagination, not one JSON payload; see _fetch_successfactors_postings
    },
    # The three below are fetched by their own functions in fetch_postings
    # (paged POST, RSS feeds); the entries keep VENDORS and ADAPTERS in step.
    "workday": {"url": None, "parse": None},
    "taleo": {"url": None, "parse": None},
    "teamtailor": {"url": None, "parse": None},
    # Fetched by jobtrack_boards.FETCHERS.
    "oracle": {"url": None, "parse": None},
    "cornerstone": {"url": None, "parse": None},
    "join": {"url": None, "parse": None},
    "avature": {"url": None, "parse": None},
    "bendingspoons": {"url": None, "parse": None},
    "mantu": {"url": None, "parse": None},
    "itjobs": {"url": None, "parse": None},
}

SMARTRECRUITERS_MAX = 1000
SMARTRECRUITERS_MAX_PAGES = 1000  # hard iteration cap, independent of offset arithmetic
SUCCESSFACTORS_MAX_PAGES = 200  # hard iteration cap; a company posting more than ~5000 roles at once is not realistic


def _get_json(url: str, timeout: int = DEFAULT_TIMEOUT):
    request = urllib.request.Request(url, headers={"User-Agent": "jobtrack-watch/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise AdapterError("HTTP {} from {}".format(error.code, url)) from None
    except (urllib.error.URLError, OSError) as error:
        raise AdapterError("could not reach {}: {}".format(url, error)) from None
    except ValueError:
        raise AdapterError("response from {} was not JSON".format(url)) from None


def _get_html(url: str, timeout: int = DEFAULT_TIMEOUT, max_bytes: int = 2_000_000) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "jobtrack-watch/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(max_bytes).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raise AdapterError("HTTP {} from {}".format(error.code, url)) from None
    except (urllib.error.URLError, OSError) as error:
        raise AdapterError("could not reach {}: {}".format(url, error)) from None


def _fetch_successfactors_postings(source: Source, timeout: int) -> List[Posting]:
    """Fetch every posting from a SuccessFactors Career Site Builder search,
    paginated via `startrow`. Always queries unfiltered (`q=`, no
    `locationsearch`) -- the site's own location search can return zero
    results for a valid query and silently fall back to an unrelated "most
    recent jobs" list (flagged by `id="noresults-message"`), which must never
    be mistaken for real matches. Location filtering happens afterwards, via
    the same generic Location column every other vendor already uses.
    """
    base_url = "https://{}".format(source.slug)
    postings: List[Posting] = []
    page_size: Optional[int] = None
    startrow = 0
    pages_fetched = 0

    while pages_fetched < SUCCESSFACTORS_MAX_PAGES:
        url = "{}/search/?q=&startrow={}".format(base_url, startrow)
        html_text = _get_html(url, timeout)
        parser = _SuccessFactorsRowParser(base_url)
        parser.feed(html_text)

        if parser.saw_noresults:
            return []
        if pages_fetched == 0 and not parser.postings and _is_successfactors_rmk(html_text):
            return _fetch_successfactors_rmk_postings(source, timeout)

        postings.extend(parser.postings)
        pages_fetched += 1

        if page_size is None:
            page_size = len(parser.postings) or None

        match = re.search(r"of\s*<b>([\d,]+)</b>", html_text)
        total = int(match.group(1).replace(",", "")) if match else len(postings)

        if not parser.postings or not page_size or startrow + page_size >= total:
            break
        startrow += page_size

    return postings


SUCCESSFACTORS_RMK_FINGERPRINTS = ("rmk-jobs-search", "rmkapp")
SUCCESSFACTORS_RMK_LOCATION_CODES = {"RMT": "Remote", "GLO": "Global"}


def _is_successfactors_rmk(html_text: str) -> bool:
    """True for the newer client-rendered RMK search (Voith), whose server
    HTML has no tr.data-row table for _SuccessFactorsRowParser to read."""
    lowered = html_text.lower()
    return any(needle in lowered for needle in SUCCESSFACTORS_RMK_FINGERPRINTS)


def _post_json(url: str, body: dict, timeout: int = DEFAULT_TIMEOUT):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"User-Agent": "jobtrack-watch/1", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise AdapterError("HTTP {} from {}".format(error.code, url)) from None
    except (urllib.error.URLError, OSError) as error:
        raise AdapterError("could not reach {}: {}".format(url, error)) from None
    except ValueError:
        raise AdapterError("response from {} was not JSON".format(url)) from None


def _rmk_location(raw) -> str:
    """"Porto, PRT, 4150-196<br/>" -> "Porto, PRT, 4150-196"; "RMT, " -> "Remote"."""
    parts = []
    for item in _as_list(raw):
        text = html.unescape(re.sub(r"<[^>]*>", " ", _as_str(item)))
        text = re.sub(r"\s+", " ", text).strip().strip(",").strip()
        text = SUCCESSFACTORS_RMK_LOCATION_CODES.get(text, text)
        if text and text not in parts:
            parts.append(text)
    return "; ".join(parts)


def _rmk_date(value) -> Optional[str]:
    """RMK's "5/13/26" (M/D/YY) as ISO, or None."""
    try:
        return datetime.datetime.strptime(_as_str(value), "%m/%d/%y").date().isoformat()
    except ValueError:
        return None


def parse_successfactors_rmk(payload, base_url: str, locale: str = "en_US") -> List[Posting]:
    """One page of the RMK search API. A page past the end carries only
    totalJobs, with no jobSearchResult key at all, so that reads as empty."""
    if not isinstance(payload, dict):
        raise AdapterError(
            "successfactors: expected a dict with a 'jobSearchResult' list, got {}".format(
                type(payload).__name__
            )
        )
    postings = []
    for entry in _as_list(payload.get("jobSearchResult")):
        job = _as_dict(_as_dict(entry).get("response"))
        job_id = _job_id(job.get("id"))
        if not job_id:
            continue  # RMK lists a few untitled shells with no id; they have no job page either
        url_title = html.unescape(_as_str(job.get("urlTitle")) or _as_str(job.get("unifiedUrlTitle")))
        postings.append(
            Posting(
                id=job_id,
                title=html.unescape(_as_str(job.get("unifiedStandardTitle"))),
                url="{}/job/{}/{}-{}/".format(base_url, url_title or "job", job_id, locale),
                location=_rmk_location(job.get("jobLocationShort")),
                posted=_rmk_date(job.get("unifiedStandardStart")),
            )
        )
    return postings


def _fetch_successfactors_rmk_postings(source: Source, timeout: int) -> List[Posting]:
    """Every posting from the RMK search API, paged by pageNumber.

    Sorted by date on purpose: the default (relevance) order is not stable
    between requests, so paging through it returned some jobs twice and
    others never (181 distinct ids out of 222 on Voith's live board), while
    the date order returned every one exactly once.
    """
    base_url = "https://{}".format(source.slug)
    url = "{}/services/recruiting/v1/jobs".format(base_url)
    postings: List[Posting] = []
    seen = set()
    raw_count = 0
    for page in range(SUCCESSFACTORS_MAX_PAGES):
        payload = _post_json(url, {
            "locale": "en_US", "pageNumber": page, "sortBy": "date", "keywords": "",
            "location": "", "facetFilters": {}, "brand": "", "skills": [],
            "categoryId": 0, "alertId": "", "rcmCandidateId": "",
        }, timeout)
        if not isinstance(payload, dict):
            raise AdapterError(
                "successfactors: expected a dict from {}, got {}".format(url, type(payload).__name__)
            )
        entries = _as_list(payload.get("jobSearchResult"))
        if not entries:
            break
        raw_count += len(entries)
        for posting in _parse_or_raise(
            lambda p, slug: parse_successfactors_rmk(p, base_url), payload, source
        ):
            if posting.id not in seen:
                seen.add(posting.id)
                postings.append(posting)
        total = payload.get("totalJobs")
        if isinstance(total, int) and not isinstance(total, bool) and raw_count >= total:
            break
    return postings


def _parse_or_raise(parse, payload, source: Source) -> List[Posting]:
    """Run a vendor parse function, turning any payload-shape surprise into AdapterError.

    parse_* is hardened against the malformed shapes we know about (None
    records, wrong-typed nested fields, wrong top-level container type) and
    should not normally raise. This is the safety net for whatever shape we
    didn't anticipate: an AttributeError/TypeError/KeyError escaping here
    would otherwise surface as a raw traceback instead of the AdapterError
    callers are documented to catch.
    """
    try:
        return parse(payload, source.slug)
    except (AttributeError, TypeError, KeyError) as error:
        raise AdapterError("could not understand payload for {}: {}".format(source, error)) from None


def _positive_int(value, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


ASHBY_BOARD_GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams"
ASHBY_BOARD_QUERY = (
    "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { "
    "jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) "
    "{ jobPostings { id title locationName } } }"
)


def parse_ashby_board(payload, slug: str) -> List[Posting]:
    """Postings from the query Ashby's own hosted board page makes."""
    board = _as_dict(_as_dict(_as_dict(payload).get("data")).get("jobBoard"))
    if not board:
        raise AdapterError("ashby has no job board named {!r}".format(slug))
    postings = []
    for job in _require_container(board, "jobPostings", "ashby"):
        job = _as_dict(job)
        job_id = _job_id(job.get("id"))
        if not job_id:
            continue
        postings.append(
            Posting(
                id=job_id,
                title=_as_str(job.get("title")),
                url="https://jobs.ashbyhq.com/{}/{}".format(slug, job_id),
                location=_as_str(job.get("locationName")),
                posted=None,
            )
        )
    return postings


def _fetch_ashby_postings(source: Source, url: str, timeout: int) -> List[Posting]:
    """Ashby's public posting API, falling back to the hosted board's own query.

    Some companies (Toggl) switch the public API off, which answers 404, while
    their board page still lists every role. Both return the same posting
    ids, so seen-state carries across whichever one answered.
    """
    try:
        payload = _get_json(url, timeout)
    except AdapterError as error:
        if not str(error).startswith("HTTP 404"):
            raise
        body = json.dumps({
            "operationName": "ApiJobBoardWithTeams",
            "variables": {"organizationHostedJobsPageName": source.slug},
            "query": ASHBY_BOARD_QUERY,
        }).encode("utf-8")
        request = urllib.request.Request(
            ASHBY_BOARD_GRAPHQL, data=body,
            headers={"User-Agent": "jobtrack-watch/1", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                board = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as http_error:
            raise AdapterError("HTTP {} from {}".format(http_error.code, ASHBY_BOARD_GRAPHQL)) from None
        except (urllib.error.URLError, OSError) as net_error:
            raise AdapterError("could not reach {}: {}".format(ASHBY_BOARD_GRAPHQL, net_error)) from None
        except ValueError:
            raise AdapterError("response from {} was not JSON".format(ASHBY_BOARD_GRAPHQL)) from None
        return _parse_or_raise(parse_ashby_board, board, source)
    return _parse_or_raise(lambda p, slug: parse_ashby(p), payload, source)


# --- Workday -------------------------------------------------------------------
#
# Slug: "<host>/<tenant>/<site>", e.g. "outsystems.wd503.myworkdayjobs.com/
# outsystems/OutSystems". The tenant is usually the host's first label, but
# the myworkdaysite.com hosts are shared, so it is spelled out.

WORKDAY_PAGE_SIZE = 20  # the jobs endpoint refuses larger pages
WORKDAY_MAX_PAGES = 100
_WORKDAY_MULTI_LOCATION = re.compile(r"^\d+ locations?$", re.IGNORECASE)


def _post_json(url: str, body: dict, timeout: int):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={
            "User-Agent": "jobtrack-watch/1",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise AdapterError("HTTP {} from {}".format(error.code, url)) from None
    except (urllib.error.URLError, OSError) as error:
        raise AdapterError("could not reach {}: {}".format(url, error)) from None
    except ValueError:
        raise AdapterError("response from {} was not JSON".format(url)) from None


def _workday_parts(slug: str) -> Tuple[str, str, str]:
    parts = slug.split("/")
    if len(parts) != 3 or not all(parts):
        raise AdapterError("workday source must be host/tenant/site, got {!r}".format(slug))
    return parts[0], parts[1], parts[2]


def _workday_board_url(host: str, tenant: str, site: str) -> str:
    if host.endswith(".myworkdaysite.com"):
        return "https://{}/recruiting/{}/{}".format(host, tenant, site)
    return "https://{}/{}".format(host, site)


def parse_workday(payload, slug: str) -> List[Posting]:
    host, tenant, site = _workday_parts(slug)
    board = _workday_board_url(host, tenant, site)
    postings = []
    for job in _require_container(payload, "jobPostings", "workday"):
        job = _as_dict(job)
        path = _as_str(job.get("externalPath"))
        if not path:
            continue
        location = _as_str(job.get("locationsText"))
        if _WORKDAY_MULTI_LOCATION.match(location.strip()):
            # "3 Locations" names none of them; treat as unknown so the
            # location filter does not hide it.
            location = ""
        postings.append(
            Posting(
                id=path,
                title=_as_str(job.get("title")),
                url=board + path,
                location=location,
                posted=None,  # Workday only gives "Posted 3 Days Ago"
            )
        )
    return postings


def _fetch_workday_postings(source: Source, timeout: int) -> List[Posting]:
    host, tenant, site = _workday_parts(source.slug)
    url = "https://{}/wday/cxs/{}/{}/jobs".format(host, tenant, site)
    postings: List[Posting] = []
    total = None
    for page in range(WORKDAY_MAX_PAGES):
        payload = _post_json(
            url,
            {"appliedFacets": {}, "limit": WORKDAY_PAGE_SIZE,
             "offset": page * WORKDAY_PAGE_SIZE, "searchText": ""},
            timeout,
        )
        batch = _parse_or_raise(parse_workday, payload, source)
        if total is None:
            # Only the first page reports the real total; later pages say 0.
            total = _positive_int(_as_dict(payload).get("total"), default=0)
        postings.extend(batch)
        if not batch or len(postings) >= total:
            break
    return postings


# --- Taleo Business Edition ------------------------------------------------------
#
# Slug: "<host>/<instance>/<org>/<cws>", e.g. "lde.tbe.taleo.net/lde02/BARHOLD/37".
# The career section's RSS feed lists every open requisition in one response,
# with location, so there is no paging. Taleo Enterprise (careersection URLs)
# is a different product and is not covered.

TALEO_MAX_BYTES = 8_000_000  # Parfois's feed is ~1 MB with full descriptions


def _taleo_parts(slug: str) -> Tuple[str, str, str, str]:
    parts = slug.split("/")
    if len(parts) != 4 or not all(parts):
        raise AdapterError("taleo source must be host/instance/org/cws, got {!r}".format(slug))
    return parts[0], parts[1], parts[2], parts[3]


def _xml_text(element, tag: str) -> str:
    found = element.find(tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_taleo(xml_text: str, slug: str) -> List[Posting]:
    import xml.etree.ElementTree as ET

    host, instance, org, cws = _taleo_parts(slug)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise AdapterError("taleo feed for {} is not valid XML: {}".format(slug, error)) from None
    ns = "{urn:TBERss}"
    postings = []
    for item in root.iter("item"):
        rid = _xml_text(item, ns + "reqId")
        if not rid:
            continue
        place = _xml_text(item, ns + "location")
        country = _xml_text(item, ns + "locationCountry")
        location = ", ".join(part for part in (place, country) if part)
        posted = None
        raw_date = _xml_text(item, "pubDate")
        if raw_date:
            try:
                posted = datetime.datetime.strptime(raw_date[:16], "%a, %d %b %Y").date().isoformat()
            except ValueError:
                posted = None
        postings.append(
            Posting(
                id=rid,
                title=_xml_text(item, "title"),
                url="https://{}/{}/ats/careers/v2/viewRequisition?org={}&cws={}&rid={}".format(
                    host, instance, org, cws, rid
                ),
                location=location,
                posted=posted,
            )
        )
    return postings


def _fetch_taleo_postings(source: Source, timeout: int) -> List[Posting]:
    host, instance, org, cws = _taleo_parts(source.slug)
    url = (
        "https://{}/{}/ats/servlet/Rss?org={}&cws={}&WebPage=SRCHR_V2&WebVersion=0&_rss_version=2"
    ).format(host, instance, org, cws)
    return parse_taleo(_get_html(url, timeout, max_bytes=TALEO_MAX_BYTES), source.slug)


# --- Teamtailor -------------------------------------------------------------------
#
# Slug: the career site's host, e.g. "careers.coverflex.com" or "acme.teamtailor.com".
# Every Teamtailor career site serves /jobs.rss with all open roles and their
# locations in a tt: namespace.

def parse_teamtailor(xml_text: str, slug: str) -> List[Posting]:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise AdapterError("teamtailor feed for {} is not valid XML: {}".format(slug, error)) from None
    ns = "{https://teamtailor.com/locations}"
    postings = []
    for item in root.iter("item"):
        guid = _xml_text(item, "guid")
        if not guid:
            continue
        places = []
        for loc in item.iter(ns + "location"):
            city, country = _xml_text(loc, ns + "city"), _xml_text(loc, ns + "country")
            name = ", ".join(part for part in (city, country) if part) or _xml_text(loc, ns + "name")
            if name:
                places.append(name)
        if _xml_text(item, "remoteStatus") in ("fully", "hybrid"):
            places.append("Remote" if _xml_text(item, "remoteStatus") == "fully" else "Hybrid")
        posted = None
        raw_date = _xml_text(item, "pubDate")
        if raw_date:
            try:
                posted = datetime.datetime.strptime(raw_date[:16], "%a, %d %b %Y").date().isoformat()
            except ValueError:
                posted = None
        postings.append(
            Posting(
                id=guid,
                title=_xml_text(item, "title"),
                url=_xml_text(item, "link"),
                location="; ".join(places),
                posted=posted,
            )
        )
    return postings


def _fetch_teamtailor_postings(source: Source, timeout: int) -> List[Posting]:
    url = "https://{}/jobs.rss".format(source.slug)
    return parse_teamtailor(_get_html(url, timeout, max_bytes=TALEO_MAX_BYTES), source.slug)


def fetch_postings(source: Source, timeout: int = DEFAULT_TIMEOUT) -> List[Posting]:
    """Fetch and normalise every posting for a source."""
    if source.vendor == "successfactors":
        return _fetch_successfactors_postings(source, timeout)
    if source.vendor == "workday":
        return _fetch_workday_postings(source, timeout)
    if source.vendor == "taleo":
        return _fetch_taleo_postings(source, timeout)
    if source.vendor == "teamtailor":
        return _fetch_teamtailor_postings(source, timeout)
    import jobtrack_boards

    if source.vendor in jobtrack_boards.FETCHERS:
        return jobtrack_boards.FETCHERS[source.vendor](source, timeout)

    adapter = ADAPTERS.get(source.vendor)
    if adapter is None:
        raise AdapterError("no adapter for vendor {!r}".format(source.vendor))

    url = adapter["url"].format(slug=source.slug)
    if source.vendor == "ashby":
        return _fetch_ashby_postings(source, url, timeout)
    payload = _get_json(url, timeout)
    postings = _parse_or_raise(adapter["parse"], payload, source)

    if source.vendor == "smartrecruiters" and isinstance(payload, dict):
        total = payload.get("totalFound", 0)
        limit = _positive_int(payload.get("limit"), default=100)
        offset = limit
        pages_fetched = 0
        while (
            offset < total
            and offset < SMARTRECRUITERS_MAX
            and pages_fetched < SMARTRECRUITERS_MAX_PAGES
        ):
            page = _get_json("{}&offset={}".format(url, offset), timeout)
            batch = _parse_or_raise(lambda payload, slug: parse_smartrecruiters(payload, slug), page, source)
            if not batch:
                break
            postings.extend(batch)
            offset += limit
            pages_fetched += 1

    return postings


class ResolveError(Exception):
    """A careers URL could not be turned into a supported vendor and slug."""


HOST_PATTERNS: List[Tuple[str, str]] = [
    # A careers site that embeds its Greenhouse board names the slug in the
    # embed script's query string; "embed" itself is never a company.
    (r"boards\.greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)", "greenhouse"),
    (r"(?:job-)?boards\.greenhouse\.io/(?!embed\b)([A-Za-z0-9_-]+)", "greenhouse"),
    (r"jobs\.eu\.lever\.co/([A-Za-z0-9_-]+)", "lever-eu"),
    (r"jobs\.lever\.co/([A-Za-z0-9_-]+)", "lever"),
    # Ashby slugs may carry a domain ("happyrobot.ai", "neurons-lab.com").
    (r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)", "ashby"),
    (r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)", "smartrecruiters"),
    (r"careers\.smartrecruiters\.com/([A-Za-z0-9_-]+)", "smartrecruiters"),
]

UNSUPPORTED_HINTS: Dict[str, str] = {
    "phenompeople": "Phenom",
    "icims.com": "iCIMS",
    # Only reached when the URL is not a Taleo Business Edition one (which
    # _match_url_vendor handles): careersection URLs are Taleo Enterprise.
    "taleo.net": "Oracle Taleo Enterprise",
    "eightfold": "Eightfold",
    "jobvite": "Jobvite",
    "apply.workable.com": "Workable",
    "recruitee.com": "Recruitee",
    "jobs.personio": "Personio",
}

CONTENT_VENDORS: Dict[str, str] = {
    "successfactors": "successfactors",
    # A Teamtailor career site on the company's own domain (careers.coverflex.com)
    # loads its images and scripts from this CDN.
    "teamtailor-cdn.com": "teamtailor",
}

_WORKDAY_URL = re.compile(
    r"https?://((?:[a-z0-9-]+\.)?wd\d+\.myworkday(?:jobs|site)\.com)(/[^\s\"'<>]*)?", re.IGNORECASE
)
_TALEO_TBE_URL = re.compile(
    r"https?://([a-z0-9-]+\.tbe\.taleo\.net)/([a-z0-9]+)/ats/[^\s\"'<>]*", re.IGNORECASE
)
_LOCALE_SEGMENT = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$")


def _workday_from_url(host: str, path: str) -> Optional[Source]:
    segments = [seg for seg in (path or "").split("/") if seg]
    if segments and _LOCALE_SEGMENT.match(segments[0]):
        segments = segments[1:]
    if host.lower().endswith(".myworkdaysite.com"):
        # /recruiting/<tenant>/<site>/...
        if len(segments) >= 3 and segments[0] == "recruiting":
            return Source("workday", "{}/{}/{}".format(host.lower(), segments[1], segments[2]))
        return None
    if re.match(r"wd\d+\.", host, re.IGNORECASE):
        return None  # a myworkdayjobs.com host always starts with the tenant
    if not segments or segments[0] in ("wday", "job"):
        return None
    return Source("workday", "{}/{}/{}".format(host.lower(), host.split(".")[0].lower(), segments[0]))


def _taleo_from_url(url: str) -> Optional[Source]:
    found = _TALEO_TBE_URL.match(url)
    if not found:
        return None
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url.replace("&amp;", "&")).query)
    org, cws = query.get("org", [""])[0], query.get("cws", [""])[0]
    if not org or not cws:
        return None
    return Source("taleo", "{}/{}/{}/{}".format(found.group(1).lower(), found.group(2), org, cws))


def _match_url_vendor(url: str) -> Optional[Source]:
    """Vendors whose slug needs more than one captured path segment."""
    found = _WORKDAY_URL.match(url)
    if found:
        return _workday_from_url(found.group(1), found.group(2) or "")
    source = _taleo_from_url(url)
    if source is not None:
        return source
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.endswith(".teamtailor.com") and host != "www.teamtailor.com" and host != "app.teamtailor.com":
        return Source("teamtailor", host)
    return None


def _match_url_vendor_in_text(text: str) -> Optional[Source]:
    """A careers page that links out to its Workday or Taleo board."""
    for pattern in (_WORKDAY_URL, _TALEO_TBE_URL):
        for found in pattern.finditer(text):
            source = _match_url_vendor(found.group(0))
            if source is not None:
                return source
    return None


def _match_content_vendor(html: str, url: str) -> Optional[Source]:
    """Detect a vendor identifiable only by a fingerprint in the page content,
    not a shared hostname. SF CSB customers each run their own vanity domain
    (EDP's is jobs.edp.com itself), so there is no HOST_PATTERNS regex to
    match against -- the slug is the pasted URL's own hostname, not a
    captured path segment.
    """
    lowered = html.lower()
    for needle, vendor in CONTENT_VENDORS.items():
        if needle in lowered:
            return Source(vendor=vendor, slug=urllib.parse.urlparse(url).netloc)
    return None


def _match_known_host(text: str) -> Optional[Source]:
    for pattern, vendor in HOST_PATTERNS:
        found = re.search(pattern, text, re.IGNORECASE)
        if found:
            return Source(vendor=vendor, slug=found.group(1))
    return None


def _parse_url_vendor(url: str) -> Optional[Source]:
    """Extract vendor from URL by parsing hostname and path, not substring search.

    This prevents false positives from URLs containing known ATS hosts in
    query strings or redirect parameters. Only the actual hostname matters.
    """
    url = (url or "").strip()
    if not url:
        return None

    # Add scheme if missing for proper parsing
    parsed_url = url if url.startswith("http") else "https://" + url
    try:
        parsed = urllib.parse.urlparse(parsed_url)
    except Exception:
        return None

    # Build hostname+path string to match against patterns
    # This ensures we're only looking at the actual host, not query strings
    netloc_and_path = parsed.netloc + "/" + parsed.path.lstrip("/")

    source = _match_url_vendor(parsed_url)
    if source is not None:
        return source
    import jobtrack_boards

    source = jobtrack_boards.resolve_from_url(parsed_url)
    if source is not None:
        return source
    for pattern, vendor in HOST_PATTERNS:
        found = re.search(pattern, netloc_and_path, re.IGNORECASE)
        if found:
            return Source(vendor=vendor, slug=found.group(1))
    return None


def _unsupported_in(text: str) -> Optional[str]:
    lowered = text.lower()
    for needle, name in UNSUPPORTED_HINTS.items():
        if needle in lowered:
            return name
    return None


def _resolve_from_html(html: str, url: str) -> Source:
    source = _match_known_host(html)
    if source is not None:
        return source
    source = _match_url_vendor_in_text(html)
    if source is not None:
        return source
    import jobtrack_boards

    source = jobtrack_boards.resolve_from_page(url, html)
    if source is not None:
        return source
    source = _match_content_vendor(html, url)
    if source is not None:
        return source
    unsupported = _unsupported_in(html)
    if unsupported:
        raise ResolveError(
            "{} runs {}, which has no adapter yet — it can be added, "
            "but it is not one of the supported boards ({})".format(
                url, unsupported, ", ".join(VENDORS)
            )
        )
    raise ResolveError(
        "could not identify a job board behind {} — supported boards are {}".format(
            url, ", ".join(VENDORS)
        )
    )


def resolve_url(url: str, fetch: bool = True, timeout: int = DEFAULT_TIMEOUT) -> Source:
    """Turn a careers URL into a supported vendor and slug, or refuse loudly."""
    url = (url or "").strip()
    if not url:
        raise ResolveError("no URL given")

    import jobtrack_boards

    blocked = jobtrack_boards.blocked_reason(url)
    if blocked:
        raise ResolveError("{} is on {}, so it cannot be watched".format(url, blocked))

    source = _parse_url_vendor(url)
    if source is not None:
        return source

    unsupported = _unsupported_in(url)
    if unsupported:
        raise ResolveError(
            "{} runs {}, which has no adapter yet — supported boards are {}".format(
                url, unsupported, ", ".join(VENDORS)
            )
        )

    if not fetch:
        raise ResolveError(
            "could not identify a job board from the URL {} — supported boards are {}".format(
                url, ", ".join(VENDORS)
            )
        )

    request_url = url if url.startswith("http") else "https://" + url
    request = urllib.request.Request(
        request_url, headers={"User-Agent": "Mozilla/5.0 (compatible; jobtrack-watch/1)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read(500000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as error:
        raise ResolveError("could not fetch {}: {}".format(request_url, error)) from None

    return _resolve_from_html(html, request_url)


FAILURE_ALERT_THRESHOLD = 2


@dataclass
class SourceState:
    seen_ids: List[str] = field(default_factory=list)
    last_ok: Optional[str] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0


def state_key(watch: Watch) -> str:
    """Key state by vendor:slug so renaming a company keeps its seen ids."""
    return str(watch.source)


def load_state(path: str) -> Dict[str, SourceState]:
    """Load state, rebuilding from empty if the file is missing or corrupt."""
    file_exists = False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        file_exists = True
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        # File exists but is corrupt; report it and rebuild from empty
        print("warning: discarding corrupt state file: {}".format(path), file=sys.stderr)
        return {}

    if not isinstance(raw, dict):
        if file_exists:
            print("warning: discarding corrupt state file: {}".format(path), file=sys.stderr)
        return {}

    state: Dict[str, SourceState] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        # Coerce last_ok and last_error to Optional[str], dropping non-strings
        last_ok = value.get("last_ok")
        if not isinstance(last_ok, str):
            last_ok = None
        last_error = value.get("last_error")
        if not isinstance(last_error, str):
            last_error = None
        # Coerce consecutive_failures defensively; any non-numeric value falls back to 0
        cf_raw = value.get("consecutive_failures", 0)
        try:
            consecutive_failures = int(cf_raw) if cf_raw is not None else 0
        except (TypeError, ValueError):
            consecutive_failures = 0
        state[key] = SourceState(
            seen_ids=[str(i) for i in value.get("seen_ids", []) if isinstance(i, (str, int))],
            last_ok=last_ok,
            last_error=last_error,
            consecutive_failures=consecutive_failures,
        )
    return state


def save_state(path: str, state: Dict[str, SourceState]) -> None:
    payload = {
        key: {
            "seen_ids": sorted(set(value.seen_ids)),
            "last_ok": value.last_ok,
            "last_error": value.last_error,
            "consecutive_failures": value.consecutive_failures,
        }
        for key, value in state.items()
    }
    jobtrack.write_log_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def matches(
    title: str,
    include: List[str],
    exclude: List[str],
    location: str = "",
    location_filter: Optional[List[str]] = None,
) -> bool:
    """Exclusions always win; empty include/location_filter lists match everything.

    Title filtering and location filtering are independent AND'd conditions:
    a posting must clear both to be reported. location/location_filter default
    to "no restriction" so every call site that predates this feature (and
    every company without a Location override) is completely unaffected.
    """
    lowered_title = (title or "").lower()
    for term in exclude:
        if term.lower() in lowered_title:
            return False
    if include and not any(term.lower() in lowered_title for term in include):
        return False
    # A posting that reports no location at all (Sonae's board) passes: an
    # unknown location is not evidence the role is somewhere else.
    if location_filter and (location or "").strip():
        lowered_location = location.lower()
        if not any(term.lower() in lowered_location for term in location_filter):
            return False
    return True


def filters_for(watch: Watch, defaults: Filters) -> Filters:
    """Per-company keywords/location override the defaults; excludes are inherited."""
    return Filters(
        include=watch.keywords if watch.keywords is not None else list(defaults.include),
        exclude=list(defaults.exclude),
        location=watch.location if watch.location is not None else list(defaults.location),
    )


@dataclass
class PollResult:
    company: str
    source: Source
    new: List[Posting] = field(default_factory=list)
    error: Optional[str] = None
    seeded: bool = False


def poll(
    watchlist: Watchlist,
    state: Dict[str, SourceState],
    today: datetime.date,
    fetch=None,
) -> List[PollResult]:
    """Fetch every source, diff against seen ids, and mutate state in place."""
    fetch = fetch or fetch_postings
    results: List[PollResult] = []

    for watch in watchlist.watches:
        key = state_key(watch)
        entry = state.setdefault(key, SourceState())
        # Never had a successful poll -- capture this BEFORE it gets
        # overwritten below with today's date on success. This, not
        # `first_run` and not the emptiness of seen_ids, is what decides
        # seeding: a source can already have a state entry (e.g. its first
        # poll failed) and still need to seed silently on its first
        # success; conversely a source can have empty seen_ids because the
        # board is legitimately empty, and a first real posting on it must
        # notify, not seed.
        never_polled = entry.last_ok is None
        result = PollResult(company=watch.company, source=watch.source)

        try:
            postings = fetch(watch.source)
        except Exception as error:  # one dead source must not stop the rest
            entry.consecutive_failures += 1
            entry.last_error = str(error)
            result.error = str(error)
            results.append(result)
            continue

        entry.consecutive_failures = 0
        entry.last_error = None
        entry.last_ok = today.isoformat()

        active = filters_for(watch, watchlist.defaults)
        seen = set(entry.seen_ids)
        fresh = [p for p in postings if p.id not in seen]

        if never_polled:
            result.seeded = True
        else:
            result.new = [
                p for p in fresh
                if matches(p.title, active.include, active.exclude, p.location, active.location)
            ]

        # every posting seen this run is recorded, matched or not
        entry.seen_ids = sorted(seen | {p.id for p in postings})
        results.append(result)

    return results


TELEGRAM_ENV_PATH = os.path.expanduser("~/.claude/channels/telegram/.env")
TELEGRAM_ACCESS_PATH = os.path.expanduser("~/.claude/channels/telegram/access.json")
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram's sendMessage hard limit is 4096 characters; we split well below
# that so header/footer text never pushes a chunk over the edge.
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_SAFE_MESSAGE_LIMIT = 3500


class NotifyError(Exception):
    """Notifications could not be sent."""


def read_telegram_credentials(
    env_path: Optional[str] = None, access_path: Optional[str] = None
) -> Tuple[str, str]:
    """Read the bot token and chat id at runtime. The token is never stored."""
    env_path = env_path or TELEGRAM_ENV_PATH
    access_path = access_path or TELEGRAM_ACCESS_PATH

    token = ""
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.strip().split("=", 1)[1].strip()
                    break
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError is a ValueError subclass; caught explicitly
        # here (not folded into a bare ValueError catch) so the exception
        # message we build never includes the raw undecodable bytes -- only
        # the file path, never the file's contents.
        raise NotifyError(
            "could not read the Telegram bot token from {}".format(env_path)
        ) from None
    if not token:
        raise NotifyError("no TELEGRAM_BOT_TOKEN found in {}".format(env_path))

    try:
        with open(access_path, "r", encoding="utf-8") as handle:
            allow = json.load(handle).get("allowFrom") or []
    except (OSError, ValueError):
        # ValueError also covers json.JSONDecodeError and UnicodeDecodeError.
        raise NotifyError("could not read a chat id from {}".format(access_path)) from None
    if not allow:
        raise NotifyError("no allowed chat id in {}".format(access_path))

    return token, str(allow[0])


def _notification_header(result: PollResult, continued: bool = False) -> str:
    suffix = " (cont.)" if continued else ""
    return "{} — {} new role(s){}".format(result.company, len(result.new), suffix)


def _posting_block(posting: Posting) -> str:
    where = " ({})".format(posting.location) if posting.location else ""
    return "• {}{}\n  {}".format(posting.title, where, posting.url)


def format_notification(result: PollResult) -> str:
    lines = [_notification_header(result), ""]
    lines.extend(_posting_block(posting) for posting in result.new)
    return "\n".join(lines)


def split_notification(
    result: PollResult, limit: int = TELEGRAM_SAFE_MESSAGE_LIMIT
) -> List[str]:
    """Split a notification into messages that each fit under `limit`.

    Breaks only between postings, never mid-posting -- a single
    over-sized posting block is left to exceed the limit on its own
    rather than being torn apart.
    """
    blocks = [_posting_block(posting) for posting in result.new]
    messages: List[str] = []
    current: List[str] = []
    continued = False

    def flush() -> None:
        header = _notification_header(result, continued=continued)
        messages.append("\n".join([header, ""] + current))

    for block in blocks:
        header = _notification_header(result, continued=continued)
        candidate = "\n".join([header, ""] + current + [block])
        if current and len(candidate) > limit:
            flush()
            continued = True
            current = [block]
        else:
            current.append(block)

    flush()
    return messages


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def send_telegram(
    text: str, token: str, chat_id: str, timeout: int = DEFAULT_TIMEOUT, opener=None
) -> None:
    opener = opener or urllib.request.urlopen
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    request = urllib.request.Request(TELEGRAM_API.format(token=token), data=data)
    try:
        with opener(request, timeout=timeout) as response:
            response.read()
    except Exception as error:
        raise NotifyError("Telegram send failed: {}".format(_redact(str(error), token))) from None


@dataclass
class NotifyReport:
    """Outcome of a notify() run, so callers can tell partial from full success
    without parsing strings."""

    sent: int = 0
    failed: List[str] = field(default_factory=list)


def notify(
    results: List[PollResult], opener=None, credentials=None, channel: Optional[str] = None
) -> NotifyReport:
    """Send new-posting messages, one company at a time, isolating failures.

    The channel comes from jobkit.toml unless given; explicit credentials mean
    Telegram. "console" prints the messages, for users without a bot.

    A send failure for one company (oversized message, network error, bad
    token, ...) is recorded in the returned report but must not stop the
    rest of the companies from being notified.
    """
    pending = [r for r in results if r.new]
    if not pending:
        return NotifyReport(sent=0, failed=[])
    config = None
    if channel is None:
        if credentials:
            channel = "telegram"
        else:
            config = jobkit_config.load()
            channel = config.notify_channel
    if channel == "console":
        sent = 0
        for result in pending:
            for message in split_notification(result):
                print(message)
                sent += 1
        return NotifyReport(sent=sent, failed=[])
    if credentials is None:
        env_path = os.path.expanduser((config or jobkit_config.load()).telegram_env)
        credentials = read_telegram_credentials(
            env_path, os.path.join(os.path.dirname(env_path), "access.json")
        )
    token, chat_id = credentials
    sent = 0
    failed: List[str] = []
    for result in pending:
        try:
            for message in split_notification(result):
                send_telegram(message, token, chat_id, opener=opener)
                sent += 1
        except NotifyError:
            failed.append(result.company)
    return NotifyReport(sent=sent, failed=failed)


def _watch_row_index(lines: List[str], company: str) -> Optional[int]:
    for index, line in enumerate(lines):
        cells = jobtrack.split_row(line)
        if cells is None or len(cells) != len(WATCH_COLUMNS):
            continue
        if cells[WCOL_COMPANY] == "Company" or jobtrack._is_separator(cells):
            continue
        if cells[WCOL_COMPANY] == company:
            return index
    return None


def _validate_watch_field(value: str, name: str) -> None:
    if not value or not value.strip():
        raise ValueError("{} must not be empty".format(name))
    if "|" in value:
        raise ValueError("{} must not contain '|' (breaks table columns)".format(name))
    if "\n" in value or "\r" in value:
        raise ValueError("{} must not contain a line break".format(name))


def _validate_watch_company(value: str) -> None:
    """A company is a watch field, plus it must not collide with the header literal.

    Mirrors jobtrack._validate_company: without this guard, add_watch(text,
    "Company", ...) would write a row that every reader (parse_watchlist,
    format_status, poll) treats as the table header and silently skips --
    the row becomes an invisible, unremovable orphan (remove_watch can never
    find it either, since _watch_row_index applies the same skip).
    """
    _validate_watch_field(value, "company")
    if value.strip() == HEADER_COMPANY:
        raise ValueError(
            "company must not be {!r}: every reader treats that row as the "
            "table header and skips it".format(HEADER_COMPANY)
        )


def _validate_watch_keyword(value: str) -> None:
    _validate_watch_field(value, "keyword")
    if "," in value:
        raise ValueError(
            "keyword must not contain ',' (splits into two keywords on the next read): {!r}".format(
                value
            )
        )


def add_watch(
    text: str, company: str, source: Source, keywords: Optional[List[str]],
    added: datetime.date, location: Optional[List[str]] = None,
) -> str:
    _validate_watch_company(company)
    for term in keywords or []:
        _validate_watch_keyword(term)
    for term in location or []:
        _validate_watch_keyword(term)

    lines = text.splitlines()
    if _watch_row_index(lines, company) is not None:
        raise jobtrack.Duplicate("{} is already on the watchlist".format(company))

    last_row = None
    for index, line in enumerate(lines):
        cells = jobtrack.split_row(line)
        if cells is not None and len(cells) == len(WATCH_COLUMNS):
            last_row = index
    if last_row is None:
        raise jobtrack.NotFound("watchlist.md has no table to append to")

    row = "| " + " | ".join(
        [
            company,
            str(source),
            ", ".join(keywords) if keywords else PLACEHOLDER,
            ", ".join(location) if location else PLACEHOLDER,
            added.isoformat(),
        ]
    ) + " |"
    lines.insert(last_row + 1, row)
    return jobtrack._rejoin(lines, text)


def remove_watch(text: str, company: str) -> str:
    lines = text.splitlines()
    index = _watch_row_index(lines, company)
    if index is None:
        raise jobtrack.NotFound("{} is not on the watchlist".format(company))
    lines.pop(index)
    return jobtrack._rejoin(lines, text)


def unsee_unnotified(
    state: Dict[str, SourceState], results: List[PollResult], failed: List[str]
) -> None:
    """Back the new MATCHED ids of every failed company out of its seen ids.

    poll() records every posting it fetched as seen, so without this a role
    whose notification never reached Telegram can never be "new" again: the
    next poll diffs against a seen set that already contains it and stays
    silent forever. Backing those ids out makes the next poll re-detect and
    re-report exactly the roles the user was never told about.

    Only the ids in result.new are removed -- the postings that did NOT match
    the filters must STAY seen. They were never going to be notified, so
    un-seeing them would buy nothing and cost everything: every subsequent
    poll would re-evaluate the whole board from scratch, forever.
    """
    failed_companies = set(failed)
    for result in results:
        if result.company not in failed_companies or not result.new:
            continue
        entry = state.get(str(result.source))
        if entry is None:  # a source polled but never recorded; nothing to undo
            continue
        unnotified = {posting.id for posting in result.new}
        entry.seen_ids = [i for i in entry.seen_ids if i not in unnotified]


def run_poll(
    watchlist_path: str,
    state_path: str,
    today: datetime.date,
    fetch=None,
    opener=None,
    notify_enabled: bool = True,
) -> List[PollResult]:
    watchlist = parse_watchlist(
        jobtrack._load(watchlist_path), filename=os.path.basename(watchlist_path)
    )
    state = load_state(state_path)
    results = poll(watchlist, state, today, fetch=fetch)
    if notify_enabled:
        # Notify FIRST, save SECOND. poll() has already marked every fetched
        # id as seen in `state`; committing that to disk before the alerts go
        # out means a failed send discards that day's roles permanently --
        # they are seen, so they are never new again, and the user is never
        # told. Under the LaunchAgent that is invisible (exit 0, a warning in
        # a log nobody reads), which is precisely the silent failure this
        # whole feature exists to eliminate.
        #
        # notify() swallows per-company send failures into report.failed; the
        # only thing that escapes it is a NotifyError from
        # read_telegram_credentials, i.e. NOTHING could be sent at all. In
        # that case do not save state either -- let it propagate with the
        # state file untouched, so the next poll re-detects everything.
        report = notify(results, opener=opener)
        if report.failed:
            unsee_unnotified(state, results, report.failed)
            print(
                "warning: notifications failed to send for: {} — those roles "
                "stay unseen and will be reported again on the next poll.".format(
                    ", ".join(report.failed)
                ),
                file=sys.stderr,
            )
        # save_state must only happen on a REAL run. poll() marks every
        # fetched id as seen in `state` regardless of notify_enabled, so
        # persisting that after a dry run would mark today's "new" postings
        # as already-seen -- the next real poll would then stay silent about
        # them forever. `--dry-run` means "no persistent side effects," full
        # stop, so its call to run_poll (notify_enabled=False) must leave the
        # state file byte-identical to how it found it.
        save_state(state_path, state)
    return results


def format_status(watchlist: Watchlist, state: Dict[str, SourceState]) -> str:
    if not watchlist.watches:
        return "Watchlist is empty. Add a company with: jobtrack watch add --url <careers URL>"
    lines = ["{} source(s) watched:".format(len(watchlist.watches)), ""]
    for watch in watchlist.watches:
        entry = state.get(state_key(watch), SourceState())
        if entry.consecutive_failures >= FAILURE_ALERT_THRESHOLD:
            health = "FAILING ({}x) — {}".format(entry.consecutive_failures, entry.last_error)
        elif entry.last_ok:
            health = "ok, last polled {}".format(entry.last_ok)
        else:
            health = "never polled"
        lines.append(
            "  {:<24} {:<28} {:>4} seen  {}".format(
                watch.company, str(watch.source), len(entry.seen_ids), health
            )
        )
    return "\n".join(lines)


# --- Following companies you applied to --------------------------------------
#
# Adding an application with a posting link follows that company: the posting
# URL is resolved to its job board and the company joins the watchlist, so the
# daily poll alerts on its new roles. Every outcome, including "that board has
# no adapter", is recorded in the follow file keyed by posting URL. That file
# is what keeps `follow-postings` from re-fetching the same pages on every run,
# and what lets the dashboard say why a company is not being watched.

def default_follow_path() -> str:
    return jobkit_paths.state_file(".jobkit-follow.json")

FOLLOWED, ALREADY, UNSUPPORTED = "followed", "already", "unsupported"


@dataclass
class FollowResult:
    company: str
    outcome: str
    source: Optional[str] = None
    detail: str = ""
    warning: Optional[str] = None

    def describe(self) -> str:
        if self.outcome == FOLLOWED:
            return "Following {} via {} for new roles.".format(self.company, self.source)
        if self.outcome == ALREADY:
            return "Already following {} ({}).".format(self.company, self.source)
        if self.needs_connector:
            return "Not following {}: {}. No connector for this board yet; build one " \
                "(CLAUDE.md, \"Job board connectors\").".format(self.company, self.detail)
        return "Not following {}: {}".format(self.company, self.detail)

    @property
    def needs_connector(self) -> bool:
        """Unsupported because nobody wrote a connector, not because the site
        blocks automated access or the page could not be loaded."""
        return self.outcome == UNSUPPORTED and (
            " which has no adapter" in self.detail or self.detail.startswith("could not identify")
        )


def load_follows(path: str) -> Dict[str, dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        print("warning: discarding corrupt follow file: {}".format(path), file=sys.stderr)
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def save_follows(path: str, follows: Dict[str, dict]) -> None:
    jobtrack.write_log_atomic(path, json.dumps(follows, indent=2, sort_keys=True) + "\n")


def _watched_match(watchlist: Watchlist, company: str, source: Source) -> Optional[Watch]:
    """A company counts as followed if its board or its name is already watched.

    Matching on the board catches naming drift between the two files
    ("OLX" in applications.md, "OLX Group" on the watchlist).
    """
    for watch in watchlist.watches:
        if str(watch.source) == str(source) or watch.company.lower() == company.lower():
            return watch
    return None


def follow_posting(
    company: str, url: str, watchlist_path: str, state_path: str,
    today: datetime.date, timeout: int = DEFAULT_TIMEOUT,
) -> FollowResult:
    """Put the company behind a posting URL on the watchlist, if its board is supported."""
    try:
        source = resolve_url(url, timeout=timeout)
    except ResolveError as error:
        return FollowResult(company, UNSUPPORTED, detail=str(error))

    text = jobtrack.read_log(watchlist_path)
    match = _watched_match(parse_watchlist(text), company, source)
    if match is not None:
        return FollowResult(match.company, ALREADY, source=str(match.source))

    new_text = add_watch(text, company, source, None, today)
    try:
        postings: Optional[List[Posting]] = fetch_postings(source, timeout)
    except AdapterError:
        # Still follow it. With no last_ok in state, the next poll seeds the
        # board silently instead of alerting on every role already open.
        postings = None
    jobtrack.write_log_atomic(watchlist_path, new_text)

    result = FollowResult(company, FOLLOWED, source=str(source))
    if postings is None:
        return result
    try:
        state = load_state(state_path)
        state[str(source)] = SourceState(
            seen_ids=sorted({p.id for p in postings}), last_ok=today.isoformat()
        )
        save_state(state_path, state)
    except OSError as error:
        result.warning = (
            "recording {}'s current postings in {} failed ({}); "
            "the next poll will seed it normally.".format(company, state_path, error)
        )
    return result


def record_follow(follows: Dict[str, dict], url: str, result: FollowResult,
                  today: datetime.date) -> None:
    follows[url] = {
        "company": result.company,
        "outcome": result.outcome,
        "source": result.source,
        "detail": result.detail,
        "checked": today.isoformat(),
    }


def follow_postings(
    apps: List["jobtrack.Application"], watchlist_path: str, state_path: str,
    follow_path: str, today: datetime.date, retry: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[FollowResult]:
    """Follow every application's company whose posting URL has not been tried yet."""
    follows = load_follows(follow_path)
    results = []
    for app in apps:
        url = (app.posting or "").strip()
        if not url.startswith("http"):
            continue
        if url in follows and not (retry and follows[url].get("outcome") == UNSUPPORTED):
            continue
        result = follow_posting(app.company, url, watchlist_path, state_path, today, timeout)
        record_follow(follows, url, result, today)
        save_follows(follow_path, follows)
        results.append(result)
    return results


def following_records(
    watchlist: Watchlist, state: Dict[str, SourceState], follows: Dict[str, dict]
) -> List[dict]:
    """One row per company for the dashboard: watched boards, then unwatchable ones."""
    rows = []
    watched = set()
    for watch in watchlist.watches:
        entry = state.get(state_key(watch), SourceState())
        if entry.consecutive_failures >= FAILURE_ALERT_THRESHOLD:
            status = "failing"
        elif entry.last_ok:
            status = "watching"
        else:
            status = "pending"
        rows.append({
            "company": watch.company,
            "status": status,
            "board": watch.source.vendor,
            "since": watch.added.isoformat(),
            "last_check": entry.last_ok or "",
            "tracked": len(entry.seen_ids),
            "note": (entry.last_error or "") if status == "failing" else "",
        })
        watched.add(watch.company.lower())
    for url, info in sorted(follows.items()):
        company = info.get("company", "")
        if info.get("outcome") != UNSUPPORTED or company.lower() in watched:
            continue
        watched.add(company.lower())
        board, note = _unwatchable_reason(info.get("detail", ""))
        rows.append({
            "company": company,
            "status": "unsupported",
            "board": board,
            "since": info.get("checked", ""),
            "last_check": "",
            "tracked": 0,
            "note": note,
        })
    return rows


def _unwatchable_reason(detail: str) -> Tuple[str, str]:
    """Turn a resolver error into (board name, one plain sentence) for the dashboard."""
    runs = re.search(r" runs (.+?), which has no adapter", detail)
    if runs:
        return runs.group(1), "Uses {}, which the watcher cannot read yet.".format(runs.group(1))
    blocked = re.search(r" is on (.+), so it cannot be watched", detail)
    if blocked:
        return "", "Posted on {}.".format(blocked.group(1))
    if detail.startswith("could not identify"):
        return "", "Its careers site is not a job board the watcher can read."
    if detail.startswith("could not fetch"):
        return "", "The posting page could not be loaded, so its job board is unknown."
    return "", detail
