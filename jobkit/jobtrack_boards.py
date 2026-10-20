"""Connectors for job boards beyond the big hosted ATS APIs.

jobtrack_watch.py holds the original vendors (Greenhouse, Lever, Ashby,
SmartRecruiters, SuccessFactors, Workday, Taleo, Teamtailor). This module adds
the rest, one section per board: how its URLs look (resolve), how its open
roles are read (fetch), and how a raw record becomes a Posting (parse).

Each fetch_* takes the Source slug and returns every open role. Each parse_*
is pure, so the tests can feed it a saved response.

Boards deliberately NOT covered, because the only way in would be to get past
an access control: Revolut's careers site (bot-protection check page) and
LinkedIn (login wall, rate limiting, terms of use). The resolver names them
so the reason shows up on the dashboard.
"""
from __future__ import annotations

import html
import importlib.util
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

import jobkit_paths
import jobtrack_watch as w
from jobtrack_watch import AdapterError, Posting, Source

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 jobtrack-watch/1"
)

VENDORS = ("oracle", "cornerstone", "join", "avature", "bendingspoons", "mantu", "itjobs")

# Hosts that can be recognised but must not be scraped. Shown verbatim as the
# reason a company is not watchable.
BLOCKED_HOSTS: Dict[str, str] = {
    "revolut.com": "Revolut's careers site, which blocks automated access",
    "linkedin.com": "LinkedIn, which does not allow automated access",
}


# --- shared helpers -------------------------------------------------------------

def _request(url: str, timeout: int, data: Optional[bytes] = None,
             headers: Optional[Dict[str, str]] = None, max_bytes: int = 5_000_000) -> str:
    merged = {"User-Agent": BROWSER_UA, "Accept-Language": "en"}
    merged.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=merged)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(max_bytes).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raise AdapterError("HTTP {} from {}".format(error.code, url)) from None
    except (urllib.error.URLError, OSError) as error:
        raise AdapterError("could not reach {}: {}".format(url, error)) from None


def _json(text: str, url: str):
    try:
        return json.loads(text)
    except ValueError:
        raise AdapterError("response from {} was not JSON".format(url)) from None


def _post_json(url: str, body: dict, timeout: int, headers: Optional[Dict[str, str]] = None):
    merged = {"Content-Type": "application/json", "Accept": "application/json"}
    merged.update(headers or {})
    return _json(_request(url, timeout, json.dumps(body).encode("utf-8"), merged), url)


def _next_data(page: str, url: str) -> dict:
    """The JSON a Next.js page embeds for its first render."""
    found = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not found:
        raise AdapterError("{} has no embedded page data (__NEXT_DATA__)".format(url))
    return w._as_dict(_json(found.group(1), url))


def _parts(slug: str, count: int, vendor: str, shape: str) -> List[str]:
    parts = slug.split("/")
    if len(parts) != count or not all(parts):
        raise AdapterError("{} source must be {}, got {!r}".format(vendor, shape, slug))
    return parts


def _text(fragment: str) -> str:
    """Visible text of an HTML fragment, whitespace collapsed."""
    fragment = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


# --- Oracle Recruiting Cloud (Oracle HCM "Candidate Experience") ------------------
#
# Slug: "<host>/<site number>", e.g. "iamjgs.fa.ocs.oraclecloud.com/CX_2".
# Public REST resource the candidate site itself calls; paged by offset.

ORACLE_PAGE_SIZE = 25
ORACLE_MAX_PAGES = 200
_ORACLE_URL = re.compile(
    r"https?://([a-z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z_-]+/sites/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def parse_oracle(payload, slug: str) -> Tuple[List[Posting], int]:
    host, site = _parts(slug, 2, "oracle", "host/site")
    items = w._require_container(payload, "items", "oracle")
    if not items:
        return [], 0
    search = w._as_dict(items[0])
    total = w._positive_int(search.get("TotalJobsCount"), default=0)
    postings = []
    for job in w._as_list(search.get("requisitionList")):
        job = w._as_dict(job)
        job_id = w._job_id(job.get("Id"))
        if not job_id:
            continue
        places = [w._as_str(job.get("PrimaryLocation"))]
        for extra in w._as_list(job.get("secondaryLocations")):
            places.append(w._as_str(w._as_dict(extra).get("Name")))
        workplace = w._as_str(job.get("WorkplaceType"))
        if workplace.lower().startswith("remote"):
            places.append("Remote")
        postings.append(Posting(
            id=job_id,
            title=w._as_str(job.get("Title")),
            url="https://{}/hcmUI/CandidateExperience/en/sites/{}/job/{}".format(host, site, job_id),
            location="; ".join(p for p in places if p),
            posted=w._as_str(job.get("PostedDate")) or None,
        ))
    return postings, total


def fetch_oracle(source: Source, timeout: int) -> List[Posting]:
    host, site = _parts(source.slug, 2, "oracle", "host/site")
    postings: List[Posting] = []
    for page in range(ORACLE_MAX_PAGES):
        url = (
            "https://{}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
            "&expand=requisitionList.secondaryLocations"
            "&finder=findReqs;siteNumber={},limit={},offset={},sortBy=POSTING_DATES_DESC"
        ).format(host, site, ORACLE_PAGE_SIZE, page * ORACLE_PAGE_SIZE)
        batch, total = w._parse_or_raise(parse_oracle, _json(_request(url, timeout), url), source)
        postings.extend(batch)
        if not batch or len(postings) >= total:
            break
    return postings


def resolve_oracle(url: str) -> Optional[Source]:
    found = _ORACLE_URL.match(url)
    return Source("oracle", "{}/{}".format(found.group(1).lower(), found.group(2))) if found else None


# --- Cornerstone OnDemand (csod.com career sites) -----------------------------------
#
# Slug: "<host>/<career site id>", e.g. "mytalent.csod.com/1". The career site
# page carries a short-lived guest token and the regional API host; the job
# search API takes that token as a bearer. Same calls the page makes.

CORNERSTONE_PAGE_SIZE = 100
CORNERSTONE_MAX_PAGES = 50
_CORNERSTONE_URL = re.compile(
    r"https?://([a-z0-9-]+\.csod\.com)/ux/ats/careersite/(\d+)", re.IGNORECASE
)


def parse_cornerstone(payload, slug: str, culture: str = "en-US") -> Tuple[List[Posting], int]:
    host, site_id = _parts(slug, 2, "cornerstone", "host/careersite id")
    tenant = host.split(".")[0]
    data = w._as_dict(w._as_dict(payload).get("data"))
    if "requisitions" not in data:
        raise AdapterError("cornerstone: expected data.requisitions, found none")
    postings = []
    for job in w._require_container(data, "requisitions", "cornerstone"):
        job = w._as_dict(job)
        rid = w._job_id(job.get("requisitionId"))
        if not rid:
            continue
        places = []
        for loc in w._as_list(job.get("locations")):
            loc = w._as_dict(loc)
            place = ", ".join(p for p in (w._as_str(loc.get("city")), w._as_str(loc.get("country"))) if p)
            if place:
                places.append(place)
        posted = None
        # Written in the career site's culture: "10/12/2026" is 12 October
        # on an en-US site and 10 December on a pt-PT one.
        raw = w._as_str(job.get("postingEffectiveDate"))
        found = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
        if found:
            first, second = int(found.group(1)), int(found.group(2))
            month, day = (first, second) if culture.lower() == "en-us" else (second, first)
            if 1 <= month <= 12 and 1 <= day <= 31:
                posted = "{}-{:02d}-{:02d}".format(found.group(3), month, day)
        postings.append(Posting(
            id=rid,
            title=w._as_str(job.get("displayJobTitle")),
            url="https://{}/ux/ats/careersite/{}/home/requisition/{}?c={}".format(host, site_id, rid, tenant),
            location="; ".join(places),
            posted=posted,
        ))
    return postings, w._positive_int(data.get("totalCount"), default=0)


def _cornerstone_session(page: str) -> Tuple[str, str, int, str]:
    token = re.search(r'"token":"([^"]+)"', page)
    cloud = re.search(r'"cloud":"([^"]+)"', page)
    if not token or not cloud:
        raise AdapterError("cornerstone career site page has no guest token")
    culture_id = re.search(r'"cultureID":(\d+)', page)
    culture_name = re.search(r'"cultureName":"([^"]+)"', page)
    return (
        token.group(1), cloud.group(1).rstrip("/") + "/",
        int(culture_id.group(1)) if culture_id else 1,
        culture_name.group(1) if culture_name else "en-US",
    )


def fetch_cornerstone(source: Source, timeout: int) -> List[Posting]:
    host, site_id = _parts(source.slug, 2, "cornerstone", "host/careersite id")
    tenant = host.split(".")[0]
    page = _request("https://{}/ux/ats/careersite/{}/home?c={}".format(host, site_id, tenant), timeout)
    token, cloud, culture_id, culture_name = _cornerstone_session(page)
    url = cloud + "rec-job-search/external/jobs"
    postings: List[Posting] = []
    for number in range(1, CORNERSTONE_MAX_PAGES + 1):
        payload = _post_json(url, {
            "careerSiteId": int(site_id), "careerSitePageId": int(site_id),
            "pageNumber": number, "pageSize": CORNERSTONE_PAGE_SIZE,
            "cultureId": culture_id, "cultureName": culture_name, "searchText": "",
            "states": [], "countryCodes": [], "cities": [], "placeID": "", "radius": None,
            "postingsWithinDays": None, "customFieldCheckboxKeys": [],
            "customFieldDropdowns": [], "customFieldRadios": [],
        }, timeout, {"Authorization": "Bearer " + token})
        batch, total = w._parse_or_raise(
            lambda p, slug: parse_cornerstone(p, slug, culture_name), payload, source
        )
        postings.extend(batch)
        if not batch or len(postings) >= total:
            break
    return postings


def resolve_cornerstone(url: str) -> Optional[Source]:
    found = _CORNERSTONE_URL.match(url)
    return Source("cornerstone", "{}/{}".format(found.group(1).lower(), found.group(2))) if found else None


# --- JOIN (join.com company pages) ------------------------------------------------
#
# Slug: the company's join.com handle, e.g. "poseidon-commscom". The company
# page embeds its open jobs in Next.js page data, paged by ?page=N.

JOIN_MAX_PAGES = 30
_JOIN_URL = re.compile(r"https?://(?:www\.)?join\.com/companies/([A-Za-z0-9_-]+)", re.IGNORECASE)


def parse_join(page_data: dict, slug: str) -> Tuple[List[Posting], int]:
    state = w._as_dict(w._as_dict(w._as_dict(page_data.get("props")).get("pageProps")).get("initialState"))
    jobs = w._as_dict(state.get("jobs"))
    if "items" not in jobs:
        raise AdapterError("join: page data has no jobs list")
    postings = []
    for job in w._require_container(jobs, "items", "join"):
        job = w._as_dict(job)
        job_id = w._job_id(job.get("id"))
        handle = w._as_str(job.get("idParam"))
        if not job_id or not handle:
            continue
        city = w._as_dict(job.get("city"))
        places = [", ".join(p for p in (w._as_str(city.get("cityName")), w._as_str(city.get("countryName"))) if p)]
        if w._as_str(job.get("workplaceType")).upper() == "REMOTE":
            places.append("Remote")
        created = w._as_str(job.get("createdAt"))
        postings.append(Posting(
            id=job_id,
            title=w._as_str(job.get("title")),
            url="https://join.com/companies/{}/{}".format(slug, handle),
            location="; ".join(p for p in places if p),
            posted=created[:10] or None,
        ))
    pages = w._positive_int(w._as_dict(jobs.get("pagination")).get("pageCount"), default=1)
    return postings, pages


def fetch_join(source: Source, timeout: int) -> List[Posting]:
    postings: List[Posting] = []
    seen = set()
    for number in range(1, JOIN_MAX_PAGES + 1):
        url = "https://join.com/companies/{}".format(source.slug)
        if number > 1:
            url += "?page={}".format(number)
        batch, pages = w._parse_or_raise(parse_join, _next_data(_request(url, timeout), url), source)
        fresh = [p for p in batch if p.id not in seen]
        seen.update(p.id for p in fresh)
        postings.extend(fresh)
        if not fresh or number >= pages:
            break
    return postings


def resolve_join(url: str) -> Optional[Source]:
    found = _JOIN_URL.match(url)
    return Source("join", found.group(1).lower()) if found else None


# --- Avature (company career portals, e.g. jobs.siemens-energy.com) ----------------
#
# Slug: "<host>/<locale>/<portal>", e.g. "jobs.siemens-energy.com/en_US/jobs".
# The portal's job list is server-rendered, 20 roles a page, paged by
# folderOffset. It shows no location (that loads per role on click), so every
# Avature posting has an unknown location and passes the location filter.

AVATURE_PAGE_SIZE = 20
AVATURE_MAX_PAGES = 250
AVATURE_PAGE_DELAY = 0.2  # seconds; Siemens Energy is ~95 pages
_AVATURE_ROLE = re.compile(
    r'<a[^>]+href="https?://[^"/]+/[^"/]+/[^"/]+/FolderDetail/[^"/]+/(\d+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.S,
)


def parse_avature(page: str, slug: str) -> List[Posting]:
    host, locale, portal = _parts(slug, 3, "avature", "host/locale/portal")
    postings, seen = [], set()
    for found in _AVATURE_ROLE.finditer(page):
        job_id, title = found.group(1), _text(found.group(2))
        if job_id in seen or not title:
            continue  # the same role is linked again from its Apply/Share buttons
        seen.add(job_id)
        postings.append(Posting(
            id=job_id,
            title=title,
            url="https://{}/{}/{}/FolderDetail/{}".format(
                host, locale, portal, found.group(0).split("/FolderDetail/")[1].split('"')[0]
            ),
            location="",
            posted=None,
        ))
    return postings


def fetch_avature(source: Source, timeout: int, sleep: Callable[[float], None] = time.sleep) -> List[Posting]:
    host, locale, portal = _parts(source.slug, 3, "avature", "host/locale/portal")
    postings: List[Posting] = []
    seen = set()
    for number in range(AVATURE_MAX_PAGES):
        if number:
            sleep(AVATURE_PAGE_DELAY)
        url = "https://{}/{}/{}/Jobs?folderOffset={}".format(host, locale, portal, number * AVATURE_PAGE_SIZE)
        batch = w._parse_or_raise(parse_avature, _request(url, timeout), source)
        fresh = [p for p in batch if p.id not in seen]
        seen.update(p.id for p in fresh)
        postings.extend(fresh)
        if not fresh:
            break
    return postings


def resolve_avature(url: str, page: str) -> Optional[Source]:
    """An Avature portal on the company's own domain, recognised by its page."""
    if "avature" not in page.lower():
        return None
    parsed = urllib.parse.urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2 or not re.match(r"^[a-z]{2}_[A-Z]{2}$", segments[0]):
        return None
    return Source("avature", "{}/{}/{}".format(parsed.netloc.lower(), segments[0], segments[1].lower()))


# --- Bending Spoons (jobs.bendingspoons.com) --------------------------------------
#
# Slug: "jobs.bendingspoons.com". Its own Next.js site embeds every open role
# in the listing page's data.

def parse_bendingspoons(page_data: dict, slug: str) -> List[Posting]:
    props = w._as_dict(w._as_dict(page_data.get("props")).get("pageProps"))
    if "list" not in props:
        raise AdapterError("bendingspoons: page data has no job list")
    postings = []
    for job in w._require_container(props, "list", "bendingspoons"):
        job = w._as_dict(job)
        job_id = w._job_id(job.get("id"))
        if not job_id or w._as_str(job.get("status")) not in ("", "active"):
            continue
        places = []
        for office in w._as_list(job.get("officeLocations")):
            if isinstance(office, str):
                places.append(office)
            else:
                office = w._as_dict(office)
                places.append(w._as_str(office.get("title")))  # "Milan (Italy)"
        if job.get("availableAsRemoteInDefaultCountries"):
            places.append("Remote")
        postings.append(Posting(
            id=job_id,
            title=w._as_str(job.get("jobTitle")),
            url="https://{}/positions/{}".format(slug, job_id),
            location="; ".join(p for p in places if p),
            posted=None,
        ))
    return postings


def fetch_bendingspoons(source: Source, timeout: int) -> List[Posting]:
    url = "https://{}/".format(source.slug)
    return w._parse_or_raise(parse_bendingspoons, _next_data(_request(url, timeout), url), source)


def resolve_bendingspoons(url: str) -> Optional[Source]:
    host = urllib.parse.urlparse(url).netloc.lower()
    return Source("bendingspoons", host) if host == "jobs.bendingspoons.com" else None


# --- Mantu group career site (careers.mantu.com) ----------------------------------
#
# Slug: the brand's slug on careers.mantu.com, e.g. "mantu" or "amaris-consulting".
# The site searches a Meilisearch index holding every brand's roles. Its
# public search key, host and index name are read from the site's own script
# bundle on each fetch, so a rotated key is picked up automatically.

MANTU_HOST = "careers.mantu.com"
MANTU_MAX_HITS = 2000


def _mantu_search_config(timeout: int) -> Tuple[str, str, str]:
    jobs_page = _request("https://{}/jobs".format(MANTU_HOST), timeout)
    bundle = re.search(r'src="(https://careers\.mantu\.com/build/assets/app-[^"]+\.js)"', jobs_page)
    if not bundle:
        raise AdapterError("mantu: could not find the site's script bundle")
    script = _request(bundle.group(1), timeout, max_bytes=10_000_000)
    found = re.search(
        r'indexName:"([^"]+)",searchClient:\w+\("(https://[^"]+)","([0-9a-f]+)"', script
    )
    if not found:
        raise AdapterError("mantu: could not find the search settings in the site's script")
    return found.group(2), found.group(3), found.group(1)


def parse_mantu(payload, slug: str) -> List[Posting]:
    postings = []
    for job in w._require_container(payload, "hits", "mantu"):
        job = w._as_dict(job)
        job_id = w._job_id(job.get("id"))
        if not job_id or w._as_str(job.get("brand_slug")) != slug:
            continue
        places = []
        for loc in w._as_list(job.get("locations")):
            parts = [p.strip() for p in w._as_str(loc).split(">")]
            if len(parts) == 2 and parts[0] != parts[1]:
                places.append("{}, {}".format(parts[1], parts[0]))  # "Portugal > Lisbon"
            elif parts and parts[0]:
                places.append(parts[0])
        published = w._as_str(job.get("web_published_date"))
        postings.append(Posting(
            id=job_id,
            title=w._as_str(job.get("title")),
            url="https://{}/brands/{}/jobs/{}".format(MANTU_HOST, slug, job_id),
            location="; ".join(places),
            posted=published[:10] or None,
        ))
    return postings


def fetch_mantu(source: Source, timeout: int) -> List[Posting]:
    host, key, index = _mantu_search_config(timeout)
    payload = _post_json(
        "{}/indexes/{}/search".format(host.rstrip("/"), index),
        {"q": "", "limit": MANTU_MAX_HITS,
         "attributesToRetrieve": ["id", "title", "locations", "brand_slug", "web_published_date"]},
        timeout, {"Authorization": "Bearer " + key},
    )
    return w._parse_or_raise(parse_mantu, payload, source)


def resolve_mantu(url: str) -> Optional[Source]:
    found = re.match(r"https?://careers\.mantu\.com/brands/([a-z0-9-]+)", url, re.IGNORECASE)
    return Source("mantu", found.group(1).lower()) if found else None


# --- ITJobs.pt (company listings on a Portuguese job board) -----------------------
#
# Slug: the company's ITJobs handle, e.g. "dellent". Read from the site's own
# search results, keeping only rows linked to that company page. robots.txt
# asks for a 1-second crawl delay between pages, which is honoured.

ITJOBS_HOST = "www.itjobs.pt"
ITJOBS_MAX_PAGES = 15
ITJOBS_CRAWL_DELAY = 1.0
_ITJOBS_ROW = re.compile(r"<li[ >].*?</li>", re.S)


def parse_itjobs(page: str, slug: str) -> List[Posting]:
    postings = []
    for row in _ITJOBS_ROW.findall(page):
        if 'href="/empresa/{}"'.format(slug) not in row:
            continue
        found = re.search(r'class="title"[^>]*href="/oferta/(\d+)/([^"]+)"[^>]*>(.*?)</a>', row, re.S)
        if not found:
            continue
        details = re.search(r'class="list-details">(.*?)</div>', row, re.S)
        location = _text(details.group(1)) if details else ""
        postings.append(Posting(
            id=found.group(1),
            title=_text(found.group(3)),
            url="https://{}/oferta/{}/{}".format(ITJOBS_HOST, found.group(1), found.group(2)),
            location=location,
            posted=None,
        ))
    return postings


def fetch_itjobs(source: Source, timeout: int, sleep: Callable[[float], None] = time.sleep) -> List[Posting]:
    postings: List[Posting] = []
    seen = set()
    for number in range(1, ITJOBS_MAX_PAGES + 1):
        if number > 1:
            sleep(ITJOBS_CRAWL_DELAY)
        url = "https://{}/emprego?q={}&sort=date&page={}".format(
            ITJOBS_HOST, urllib.parse.quote(source.slug), number
        )
        page = _request(url, timeout)
        batch = [p for p in w._parse_or_raise(parse_itjobs, page, source) if p.id not in seen]
        seen.update(p.id for p in batch)
        postings.extend(batch)
        if "page={}".format(number + 1) not in page:
            break
    return postings


def resolve_itjobs(url: str, page: Optional[str]) -> Optional[Source]:
    found = re.match(r"https?://(?:www\.)?itjobs\.pt/empresa/([a-z0-9-]+)", url, re.IGNORECASE)
    if found:
        return Source("itjobs", found.group(1).lower())
    if page is not None and re.match(r"https?://(?:www\.)?itjobs\.pt/oferta/", url, re.IGNORECASE):
        company = re.search(r'href="(?:https://www\.itjobs\.pt)?/empresa/([a-z0-9-]+)"', page)
        if company:
            return Source("itjobs", company.group(1).lower())
    return None


# --- registry ---------------------------------------------------------------------

FETCHERS: Dict[str, Callable[[Source, int], List[Posting]]] = {
    "oracle": fetch_oracle,
    "cornerstone": fetch_cornerstone,
    "join": fetch_join,
    "avature": fetch_avature,
    "bendingspoons": fetch_bendingspoons,
    "mantu": fetch_mantu,
    "itjobs": fetch_itjobs,
}


# A user's own connectors, from connectors_local.py in their data repo, for
# boards that are not in jobkit yet. They are merged into the registries by
# load_local_connectors(), which the jobkit CLI calls at startup.
LOCAL_CONNECTORS = "connectors_local.py"
_LOCAL_URL_RESOLVERS: List[Callable[[str], Optional[Source]]] = []
_LOCAL_PAGE_RESOLVERS: List[Callable[[str, str], Optional[Source]]] = []


def load_local_connectors(root: Optional[str] = None) -> List[str]:
    path = os.path.join(root or jobkit_paths.data_root(), LOCAL_CONNECTORS)
    if not os.path.exists(path):
        return []
    spec = importlib.util.spec_from_file_location("connectors_local", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    added: List[str] = []
    for vendor, fetch in getattr(module, "FETCHERS", {}).items():
        if vendor in FETCHERS or vendor in w.ADAPTERS:
            raise ValueError("{}: vendor {!r} already exists in jobkit".format(LOCAL_CONNECTORS, vendor))
        FETCHERS[vendor] = fetch
        w.ADAPTERS[vendor] = {"url": None, "parse": None}
        added.append(vendor)
    if added:
        w.VENDORS = w.VENDORS + tuple(added)
    _LOCAL_URL_RESOLVERS.extend(getattr(module, "URL_RESOLVERS", ()))
    _LOCAL_PAGE_RESOLVERS.extend(getattr(module, "PAGE_RESOLVERS", ()))
    return added


def resolve_from_url(url: str) -> Optional[Source]:
    """Boards recognisable from the URL alone, without fetching it."""
    for resolve in list(_LOCAL_URL_RESOLVERS) + [resolve_oracle, resolve_cornerstone, resolve_join,
                                                 resolve_bendingspoons, resolve_mantu]:
        source = resolve(url)
        if source is not None:
            return source
    return resolve_itjobs(url, None)


def resolve_from_page(url: str, page: str) -> Optional[Source]:
    """Boards only recognisable from the fetched page."""
    for resolve in _LOCAL_PAGE_RESOLVERS:
        source = resolve(url, page)
        if source is not None:
            return source
    return resolve_itjobs(url, page) or resolve_avature(url, page)


def blocked_reason(url: str) -> Optional[str]:
    host = urllib.parse.urlparse(url if "://" in url else "https://" + url).netloc.lower()
    for domain, reason in BLOCKED_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return reason
    return None
