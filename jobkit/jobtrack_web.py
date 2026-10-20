"""Loopback-only web UI for the job application tracker."""

from __future__ import annotations

import datetime
import http.server
import json
import os
import secrets
import webbrowser
from typing import Any, Dict, Optional, Tuple

import jobtrack
import jobtrack_watch

HOST = "127.0.0.1"
# The server binds to HOST, but the browser sends back whatever hostname the
# user typed. Opening the page on http://localhost:PORT is the same server, so
# all the loopback spellings have to be accepted as same-origin or every write
# from that tab is rejected.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
TOKEN_HEADER = "X-Jobtrack-Token"


def _application_to_json(app: jobtrack.Application) -> Dict[str, Any]:
    return {
        "company": app.company,
        "role": app.role,
        "applied": app.applied.isoformat(),
        "cv": app.cv,
        "status": app.status,
        "last_contact": app.last_contact.isoformat(),
        "posting": app.posting,
        "events": [
            {"date": event.date.isoformat(), "text": event.text} for event in app.events
        ],
    }


def build_payload(path: str, today: datetime.date) -> Dict[str, Any]:
    """Read the log from disk and return everything the page needs."""
    apps = jobtrack.parse_log(jobtrack.read_log(path))
    return {
        "applications": [_application_to_json(app) for app in apps],
        "stale": jobtrack.stale_to_json(jobtrack.find_stale(apps, today)),
        "stats": jobtrack.stats_to_json(jobtrack.compute_stats(apps)),
        "statuses": list(jobtrack.STATUSES),
    }


def build_watchlist_payload(watchlist_path: str, state_path: str) -> Dict[str, Any]:
    """Read the watchlist and state from disk and return everything the page needs."""
    watchlist = jobtrack_watch.parse_watchlist(
        jobtrack.read_log(watchlist_path), filename=os.path.basename(watchlist_path)
    )
    state = jobtrack_watch.load_state(state_path)
    rows = []
    for watch in watchlist.watches:
        entry = state.get(jobtrack_watch.state_key(watch), jobtrack_watch.SourceState())
        rows.append({
            "company": watch.company,
            "source": str(watch.source),
            "vendor": watch.source.vendor,
            "keywords": watch.keywords,
            "added": watch.added.isoformat(),
            "seen": len(entry.seen_ids),
            "last_ok": entry.last_ok,
            "last_error": entry.last_error,
            "failing": entry.consecutive_failures >= jobtrack_watch.FAILURE_ALERT_THRESHOLD,
        })
    return {
        "watches": rows,
        "defaults": {"include": watchlist.defaults.include, "exclude": watchlist.defaults.exclude},
        "vendors": list(jobtrack_watch.VENDORS),
    }


_PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Applications</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #fbfbfa; --fg: #1a1a1a; --muted: #6b6b6b;
  --line: #e2e2df; --card: #ffffff; --warn: #b45309; --warnbg: #fef3c7;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16171a; --fg: #e8e8e6; --muted: #9a9a97;
    --line: #2c2e33; --card: #1e2024; --warn: #fbbf24; --warnbg: #2e2413;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.4rem; margin: 0 0 1.5rem; }
h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.06em;
     color: var(--muted); margin: 2rem 0 0.75rem; }
section { background: var(--card); border: 1px solid var(--line);
          border-radius: 10px; padding: 1rem 1.25rem; }
#attention ul { margin: 0; padding-left: 1.1rem; }
#attention li { margin: 0.35rem 0; }
#attention .who { font-weight: 600; }
#attention .why { color: var(--warn); }
.empty { color: var(--muted); margin: 0; }
.grid { display: flex; flex-wrap: wrap; gap: 1.75rem; }
.metric .value { font-size: 1.5rem; font-weight: 600; }
.metric .label { color: var(--muted); font-size: 0.82rem; }
.caveat { color: var(--muted); font-size: 0.82rem; margin: 0.9rem 0 0; }
.bycv { margin: 1.1rem 0 0; padding-top: 0.9rem; border-top: 1px solid var(--line); }
.bycv h3 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
           color: var(--muted); font-weight: 600; margin: 0 0 0.5rem; }
.bycv ul { list-style: none; margin: 0; padding: 0;
           display: flex; flex-wrap: wrap; gap: 0.3rem 1.6rem; }
.bycv li { font-size: 0.85rem; }
.bycv .cv { font-weight: 600; }
.bycv .rate { color: var(--muted); }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 0.55rem 0.6rem; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
     color: var(--muted); cursor: pointer; user-select: none; }
tr.stale td { background: var(--warnbg); }
.days { color: var(--warn); font-size: 0.8rem; white-space: nowrap; }
select, input, button { font: inherit; color: inherit; background: var(--bg);
                        border: 1px solid var(--line); border-radius: 6px;
                        padding: 0.25rem 0.4rem; }
button { cursor: pointer; }
.events { margin: 0.4rem 0 0; padding-left: 1rem; color: var(--muted); font-size: 0.85rem; }
.addevent { display: flex; gap: 0.35rem; margin-top: 0.45rem; }
.addevent input[type=text] { flex: 1; min-width: 6rem; }
#error { color: var(--warn); margin: 1rem 0 0; }
/* Deliberately outside <section id="watchlist">, which renderWatchlist()
   empties on every re-render -- a message written in here has to survive the
   re-render that follows the response it came from. */
#wlerror { color: var(--warn); margin: 0.55rem 0 0; }
#wlerror:empty { margin: 0; }
.wl-defaults { color: var(--muted); font-size: 0.85rem; margin: 0 0 0.9rem; }
.wl-warn { color: var(--warn); }
.wl-add { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.9rem;
          padding-top: 0.9rem; border-top: 1px solid var(--line); }
.wl-add input[type=text] { flex: 1; min-width: 8rem; }
</style>
<main>
  <h1>Applications</h1>
  <h2>Needs attention</h2>
  <section id="attention"></section>
  <h2>Stats</h2>
  <section id="stats"></section>
  <h2>Watchlist</h2>
  <section id="watchlist"></section>
  <p id="wlerror"></p>
  <h2>All applications</h2>
  <section><table id="table"></table></section>
  <p id="error"></p>
</main>
<script>
const TOKEN = "__TOKEN__";
const STATUSES = __STATUSES__;
let data = null;
let watchData = null;
let sortKey = "applied";
let sortAsc = false;

const el = (tag, props = {}, kids = []) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const kid of kids) node.append(kid);
  return node;
};
// Two error slots, chosen by the same `target` the response payload uses.
// Watchlist failures render immediately under the add form; everything else
// renders in the page-level slot at the bottom of <main>. A rejected add used
// to report its reason ("... runs Workday, which has no adapter yet") only in
// that bottom slot -- below the entire applications table, thousands of pixels
// from the button just clicked -- so the add looked like it had silently done
// nothing at all.
const fail = (message, target) => {
  const id = target === "watchlist" ? "wlerror" : "error";
  document.getElementById(id).textContent = message || "";
};

// Resolves true when the change was saved, false when it was not. Network and
// parse failures are turned into a visible message rather than an unhandled
// rejection, which used to leave the UI showing nothing at all after a failed
// write. Call sites still add .catch for anything render() itself throws.
//
// `target` names which module-level state the response payload updates:
// omitted (or anything other than "watchlist") updates `data`, the
// applications payload; "watchlist" updates `watchData`. The two POST
// families return different payload shapes, so a single `data = payload`
// can't serve both.
async function send(path, body, target) {
  fail("", target);
  let response, payload;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jobtrack-Token": TOKEN },
      body: JSON.stringify(body),
    });
    payload = await response.json();
  } catch (error) {
    fail("Could not reach the tracker — the change was not saved.", target);
    return false;
  }
  if (!response.ok) { fail(payload.error || "Request failed", target); return false; }
  if (target === "watchlist") { watchData = payload; } else { data = payload; }
  // A 200 can still carry a `warning` (e.g. the watchlist row was written
  // but seeding its poll state afterwards failed) -- the change was saved,
  // so this must not be reported as though it wasn't, but the user still
  // needs to see it rather than have it pass by silently.
  fail(payload.warning || "", target);
  render();
  return true;
}

function renderAttention() {
  const host = document.getElementById("attention");
  host.textContent = "";
  if (!data.stale.length) {
    host.append(el("p", { className: "empty", textContent: "Nothing overdue." }));
    return;
  }
  const list = el("ul");
  for (const item of data.stale) {
    list.append(el("li", {}, [
      el("span", { className: "who", textContent: item.company + " — " + item.role }),
      document.createTextNode(" "),
      el("span", { className: "why", textContent: item.reason }),
    ]));
  }
  host.append(list);
}

function metric(value, label) {
  return el("div", { className: "metric" }, [
    el("div", { className: "value", textContent: value }),
    el("div", { className: "label", textContent: label }),
  ]);
}

function byCvBreakdown(stats) {
  const variants = Object.keys(stats.by_cv || {}).sort();
  if (!variants.length) return null;
  const list = el("ul");
  for (const variant of variants) {
    const entry = stats.by_cv[variant];
    const percent = entry.total ? Math.round((entry.responded / entry.total) * 100) : 0;
    list.append(el("li", {}, [
      el("span", { className: "cv", textContent: variant }),
      document.createTextNode(" "),
      el("span", {
        className: "rate",
        textContent: entry.responded + "/" + entry.total + " (" + percent + "%)",
      }),
    ]));
  }
  return el("div", { className: "bycv" }, [
    el("h3", { textContent: "Response rate by CV variant" }),
    list,
  ]);
}

function renderStats() {
  const stats = data.stats;
  const host = document.getElementById("stats");
  host.textContent = "";
  const median = stats.median_days_to_first_response;
  host.append(el("div", { className: "grid" }, [
    metric(stats.total, "logged"),
    metric(Math.round(stats.response_rate * 100) + "%", "response rate"),
    metric(median === null ? "—" : median, "median days to reply"),
    metric(stats.by_status.interview + stats.by_status.screening, "in process"),
  ]));
  const breakdown = byCvBreakdown(stats);
  if (breakdown) host.append(breakdown);
  if (stats.small_sample) {
    host.append(el("p", {
      className: "caveat",
      textContent: "Sample is too small to draw conclusions from yet.",
    }));
  }
}

function watchHealthCell(watch) {
  if (watch.failing) {
    return el("span", { className: "wl-warn", textContent: watch.last_error || "failing" });
  }
  if (watch.last_ok) {
    return el("span", { textContent: "last polled " + watch.last_ok });
  }
  return el("span", { className: "empty", textContent: "never polled" });
}

function watchRemoveCell(watch) {
  const cell = el("td");
  const button = el("button", { type: "button", textContent: "Remove" });
  button.addEventListener("click", () => {
    send("/api/watchlist/remove", { company: watch.company }, "watchlist")
      .catch(() => fail("Something went wrong updating the page.", "watchlist"));
  });
  cell.append(button);
  return cell;
}

function renderWatchTable(host) {
  const table = el("table");
  const head = el("tr", {}, [
    el("th", { textContent: "Company" }),
    el("th", { textContent: "Source" }),
    el("th", { textContent: "Keywords" }),
    el("th", { textContent: "Seen" }),
    el("th", { textContent: "Health" }),
    el("th", {}),
  ]);
  table.append(el("thead", {}, [head]));

  const body = el("tbody");
  for (const watch of watchData.watches) {
    const row = el("tr");
    row.append(el("td", { textContent: watch.company }));
    row.append(el("td", { textContent: watch.source }));
    row.append(el("td", {
      textContent: watch.keywords && watch.keywords.length ? watch.keywords.join(", ") : "default",
    }));
    row.append(el("td", { textContent: String(watch.seen) }));
    row.append(el("td", {}, [watchHealthCell(watch)]));
    row.append(watchRemoveCell(watch));
    body.append(row);
  }
  table.append(body);
  host.append(table);
}

// Submitting requires a resolve plus a validating fetch against the vendor's
// API, which can take several seconds -- a button with no visible reaction
// to a click that slow reads as broken. The button is disabled and relabelled
// for the duration and always restored, on both the success and failure path.
function renderAddForm(host) {
  const url = el("input", { type: "text", placeholder: "careers URL" });
  const company = el("input", { type: "text", placeholder: "company (optional)" });
  const keywords = el("input", { type: "text", placeholder: "keywords, comma-separated (optional)" });
  const button = el("button", { type: "button", textContent: "Add" });

  button.addEventListener("click", () => {
    if (!url.value.trim()) { fail("A careers URL is required.", "watchlist"); return; }
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = "Resolving…";
    const body = { url: url.value.trim() };
    if (company.value.trim()) body.company = company.value.trim();
    if (keywords.value.trim()) body.keywords = keywords.value.trim();
    send("/api/watchlist/add", body, "watchlist")
      .then((saved) => {
        if (saved) { url.value = ""; company.value = ""; keywords.value = ""; }
      })
      .catch(() => fail("Something went wrong updating the page.", "watchlist"))
      .finally(() => {
        button.disabled = false;
        button.textContent = originalLabel;
      });
  });

  host.append(el("div", { className: "wl-add" }, [url, company, keywords, button]));
}

function renderWatchlist() {
  const host = document.getElementById("watchlist");
  host.textContent = "";
  const defaults = watchData.defaults;
  host.append(el("p", {
    className: "wl-defaults",
    textContent: "include: " + (defaults.include.join(", ") || "(none)")
      + "  ·  exclude: " + (defaults.exclude.join(", ") || "(none)"),
  }));
  if (watchData.watches.length) {
    renderWatchTable(host);
  } else {
    host.append(el("p", { className: "empty", textContent: "No companies watched yet." }));
  }
  renderAddForm(host);
}

function statusCell(app) {
  const select = el("select");
  for (const status of STATUSES) {
    select.append(el("option", { value: status, textContent: status, selected: status === app.status }));
  }
  select.addEventListener("change", () => {
    send("/api/status", { company: app.company, role: app.role, status: select.value })
      .catch(() => fail("Something went wrong updating the page."));
  });
  return select;
}

function eventsCell(app) {
  const cell = el("td");
  if (app.events.length) {
    const list = el("ul", { className: "events" });
    for (const event of app.events) {
      list.append(el("li", { textContent: event.date + " " + event.text }));
    }
    cell.append(list);
  }
  const date = el("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
  const text = el("input", { type: "text", placeholder: "what happened" });
  const button = el("button", { type: "button", textContent: "Log" });
  button.addEventListener("click", () => {
    if (!text.value.trim()) return;
    send("/api/event", {
      company: app.company, role: app.role, date: date.value, text: text.value.trim(),
    })
      .then((saved) => { if (saved) text.value = ""; })
      .catch(() => fail("Something went wrong updating the page."));
  });
  cell.append(el("div", { className: "addevent" }, [date, text, button]));
  return cell;
}

function renderTable() {
  const table = document.getElementById("table");
  table.textContent = "";
  const columns = [
    ["company", "Company"], ["role", "Role"], ["applied", "Applied"],
    ["cv", "CV"], ["status", "Status"], ["last_contact", "Last contact"],
  ];
  const head = el("tr");
  for (const [key, label] of columns) {
    const cell = el("th", { textContent: label });
    cell.addEventListener("click", () => {
      sortAsc = sortKey === key ? !sortAsc : true;
      sortKey = key;
      render();
    });
    head.append(cell);
  }
  head.append(el("th", { textContent: "Events" }));
  table.append(el("thead", {}, [head]));

  const overdue = new Map(data.stale.map((item) => [item.company + "\\u0000" + item.role, item]));
  const rows = data.applications.slice().sort((a, b) => {
    const left = String(a[sortKey]), right = String(b[sortKey]);
    return sortAsc ? left.localeCompare(right) : right.localeCompare(left);
  });

  const body = el("tbody");
  for (const app of rows) {
    const flag = overdue.get(app.company + "\\u0000" + app.role);
    const row = el("tr", { className: flag ? "stale" : "" });
    row.append(el("td", { textContent: app.company }));
    row.append(el("td", { textContent: app.role }));
    row.append(el("td", { textContent: app.applied }));
    row.append(el("td", { textContent: app.cv }));
    row.append(el("td", {}, [statusCell(app)]));
    row.append(el("td", {}, flag
      ? [document.createTextNode(app.last_contact), el("div", { className: "days", textContent: flag.days_silent + " days" })]
      : [document.createTextNode(app.last_contact)]));
    row.append(eventsCell(app));
    body.append(row);
  }
  table.append(body);
}

// data and watchData load from two independent fetches below and can finish
// in either order, so each render function only runs once its own state is
// present rather than assuming both loaded together.
function render() {
  if (data) {
    renderAttention();
    renderStats();
    renderTable();
  }
  if (watchData) renderWatchlist();
}

fetch("/api/applications")
  .then((response) => response.json())
  .then((payload) => {
    if (payload.error) { fail(payload.error); return; }
    data = payload;
    render();
  })
  .catch(() => fail("Could not load applications."));

fetch("/api/watchlist")
  .then((response) => response.json())
  .then((payload) => {
    if (payload.error) { fail(payload.error, "watchlist"); return; }
    watchData = payload;
    render();
  })
  .catch(() => fail("Could not load the watchlist.", "watchlist"));
</script>
</html>
"""


def render_page(token: str) -> str:
    """The whole UI as one self-contained document."""
    return _PAGE.replace("__TOKEN__", token).replace(
        "__STATUSES__", json.dumps(list(jobtrack.STATUSES))
    )


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "jobtrack"
    log_path = ""
    watchlist_path = ""
    state_path = ""
    token = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # keep the terminal usable

    responded = False

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.responded = True
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _error(self, code: int, message: str) -> None:
        # A failure raised *after* the success response already started writing
        # must not append a second response to the same connection.
        if self.responded:
            return
        self._send_json(code, {"error": message})

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/":
            self._send(200, render_page(self.token).encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/api/applications":
            try:
                self._send_json(200, build_payload(self.log_path, datetime.date.today()))
            except jobtrack.ParseError as error:
                self._error(500, str(error))
            except FileNotFoundError:
                self._error(500, "applications.md not initialised at {}".format(self.log_path))
            except OSError as error:
                # Unreadable file, bad permissions, full disk: without this the
                # exception escapes and the browser sees a dropped connection
                # instead of an error it can display.
                self._error(500, "could not read {}: {}".format(self.log_path, error))
            return
        if self.path == "/api/watchlist":
            try:
                self._send_json(200, build_watchlist_payload(self.watchlist_path, self.state_path))
            except jobtrack.ParseError as error:
                self._error(500, str(error))
            except FileNotFoundError:
                self._error(500, "watchlist.md not initialised at {}".format(self.watchlist_path))
            except OSError as error:
                self._error(500, "could not read {}: {}".format(self.watchlist_path, error))
            return
        self._error(404, "no such path")

    def _allowed_origins(self) -> Tuple[str, ...]:
        port = self.server.server_address[1]
        return tuple("http://{}:{}".format(host, port) for host in LOOPBACK_HOSTS)

    def _authorisation_error(self) -> Optional[str]:
        """Why the request is refused, or None if it is allowed.

        Token and origin are reported separately: telling someone who opened
        the page on localhost that their token is invalid points at entirely
        the wrong thing.
        """
        if self.headers.get(TOKEN_HEADER) != self.token:
            return "missing or invalid token"
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self._allowed_origins():
            return "origin {} is not this server (expected one of: {})".format(
                origin, ", ".join(self._allowed_origins())
            )
        return None

    def _read_body(self) -> Tuple[bool, Dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False, {}
        if not isinstance(payload, dict):
            # Well-formed JSON (a bare array/string/number/null) is still not
            # a usable request body: payload.get(...) below requires a dict.
            # Route it through the same clean-400 path as unparseable JSON
            # rather than letting an AttributeError escape do_POST uncaught.
            return False, {}
        return True, payload

    @staticmethod
    def _text(payload: Dict[str, Any], name: str) -> str:
        """A field the handler passes on as text. JSON allows any type there.

        Checked explicitly rather than relying on the writers: {"company": 123}
        used to reach jobtrack's '|' in value check and raise TypeError, which
        is a server bug, not a bad-request message. Naming the field and its
        type gives the caller something actionable. TypeError is also in
        do_POST's caught set as a backstop for any path this misses.
        """
        value = payload.get(name)
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(
                "{} must be a string, got {}".format(name, type(value).__name__)
            )
        return value

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        watchlist_paths = ("/api/watchlist/add", "/api/watchlist/remove")
        if self.path not in ("/api/status", "/api/event") + watchlist_paths:
            self._error(404, "no such path")
            return
        denial = self._authorisation_error()
        if denial is not None:
            self._error(403, denial)
            return

        ok, payload = self._read_body()
        if not ok:
            self._error(400, "body must be a JSON object")
            return

        if self.path in watchlist_paths:
            self._handle_watchlist_post(payload)
            return

        try:
            company = self._text(payload, "company")
            role = self._text(payload, "role")
            text = jobtrack.read_log(self.log_path)
            if self.path == "/api/status":
                text = jobtrack.set_status(text, company, role, self._text(payload, "status"))
            else:
                when = datetime.date.fromisoformat(self._text(payload, "date"))
                # Emptiness is jobtrack's rule to enforce, on every write path.
                text = jobtrack.append_event(
                    text, company, role, when, self._text(payload, "text").strip()
                )
            jobtrack.write_log_atomic(self.log_path, text)
            # Inside the try on purpose: build_payload re-reads and re-parses
            # the file, so it can fail after the write succeeded. Outside, that
            # escaped do_POST and dropped the connection with no response.
            self._send_json(200, build_payload(self.log_path, datetime.date.today()))
        except jobtrack.NotFound as error:
            self._error(404, str(error))
        except FileNotFoundError:
            self._error(500, "applications.md not initialised at {}".format(self.log_path))
        except OSError as error:
            self._error(500, "could not read or write {}: {}".format(self.log_path, error))
        except (ValueError, TypeError, jobtrack.ParseError) as error:
            self._error(400, str(error))

    def _handle_watchlist_post(self, payload: Dict[str, Any]) -> None:
        try:
            warning = None
            if self.path == "/api/watchlist/add":
                warning = self._add_watch(payload)
            else:
                company = self._text(payload, "company")
                text = jobtrack_watch.remove_watch(
                    jobtrack.read_log(self.watchlist_path), company
                )
                jobtrack.write_log_atomic(self.watchlist_path, text)
            # Inside the try on purpose, same reasoning as the applications
            # routes above: this re-reads and re-parses the file after the
            # write, so it can still fail after the write succeeded.
            response_payload = build_watchlist_payload(self.watchlist_path, self.state_path)
            if warning:
                # The watchlist write already committed by this point (see
                # _add_watch) -- a failure from here on is reported as a
                # warning on an otherwise-200 response, never as an error
                # implying the add didn't happen.
                response_payload["warning"] = warning
            self._send_json(200, response_payload)
        except jobtrack_watch.ResolveError as error:
            # Verbatim: this message ("runs SAP SuccessFactors, which has no
            # adapter yet") is the whole point of the feature -- it tells the
            # user why a company can't be watched, not just that it failed.
            self._error(400, str(error))
        except jobtrack_watch.AdapterError as error:
            self._error(400, str(error))
        except jobtrack.NotFound as error:
            self._error(404, str(error))
        except (jobtrack.Duplicate, ValueError) as error:
            self._error(400, str(error))
        except FileNotFoundError:
            self._error(500, "watchlist.md not initialised at {}".format(self.watchlist_path))
        except OSError as error:
            # By this point, any state.json failure has already been caught
            # and turned into a warning inside _add_watch (below) rather than
            # an exception reaching here -- so an OSError surfacing at this
            # level can only come from reading or writing watchlist_path
            # itself, and naming it here is accurate, not an assumption.
            self._error(500, "could not read or write {}: {}".format(self.watchlist_path, error))
        except (TypeError, jobtrack.ParseError) as error:
            self._error(400, str(error))

    def _add_watch(self, payload: Dict[str, Any]) -> Optional[str]:
        """Add a company to the watchlist. Returns None on full success, or a
        warning message if the watchlist row was written but seeding its poll
        state afterwards failed.

        Once write_log_atomic below succeeds, the company IS on the
        watchlist -- any exception after that point must never be reported
        as the add having failed (a client that sees an error retries the
        identical add and gets a spurious 400 "already on the watchlist" for
        an operation that, in fact, already succeeded). A subsequent
        load_state/save_state failure is caught here and downgraded to a
        warning instead of letting it fall through to the generic OSError
        handler, which would otherwise blame watchlist_path for a state.json
        problem. This also self-heals: a state entry with last_ok=None seeds
        silently on the next poll rather than notifying on every existing
        posting, so nothing is lost by leaving that entry unseeded until then.
        """
        url = self._text(payload, "url")
        if not url.strip():
            raise ValueError("url must not be empty")
        today = datetime.date.today()

        source = jobtrack_watch.resolve_url(url)
        company = self._text(payload, "company").strip() or source.slug
        keywords_field = self._text(payload, "keywords").strip()
        keywords = (
            [term.strip() for term in keywords_field.split(",") if term.strip()]
            if keywords_field
            else None
        )

        # Validate (including the duplicate-company check inside add_watch)
        # BEFORE the network fetch below -- a duplicate add is then rejected
        # without ever touching the network, mirroring the `jobtrack watch
        # add` CLI command.
        text = jobtrack_watch.add_watch(
            jobtrack.read_log(self.watchlist_path), company, source, keywords, today
        )
        postings = jobtrack_watch.fetch_postings(source)
        jobtrack.write_log_atomic(self.watchlist_path, text)

        try:
            state = jobtrack_watch.load_state(self.state_path)
            state[str(source)] = jobtrack_watch.SourceState(
                seen_ids=sorted({posting.id for posting in postings}), last_ok=today.isoformat()
            )
            jobtrack_watch.save_state(self.state_path, state)
        except OSError as error:
            return (
                "{} was added, but recording its poll history in {} failed ({}); "
                "the next poll will seed it normally.".format(company, self.state_path, error)
            )
        return None


def build_server(
    path: str,
    port: int,
    token: str,
    watchlist_path: Optional[str] = None,
    state_path: Optional[str] = None,
) -> http.server.HTTPServer:
    """Build a loopback-bound server. Port 0 picks a free port (used by tests)."""
    watchlist_path = watchlist_path or jobtrack_watch.default_watchlist_path()
    state_path = state_path or jobtrack_watch.default_state_path()
    handler = type(
        "_BoundHandler",
        (_Handler,),
        {
            "log_path": path,
            "token": token,
            "watchlist_path": watchlist_path,
            "state_path": state_path,
        },
    )
    return http.server.HTTPServer((HOST, port), handler)


def serve(path: str, port: int = 8765) -> None:
    token = secrets.token_urlsafe(32)
    server = build_server(path, port, token)
    url = "http://{}:{}/".format(HOST, server.server_address[1])
    print("jobkit UI on {}".format(url))
    print("Reading and writing {}".format(path))
    print("Ctrl-C to stop.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
