from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import jobtrack
import jobtrack_watch
import jobtrack_web

LOG = """# Applications

| Company | Role | Applied | CV | Status | Last contact | Posting |
|---|---|---|---|---|---|---|
| Northwind | Project Manager | 2026-07-24 | cv_northwind | applied | 2026-07-24 | — |
| Globex | Product Manager | 2026-07-27 | cv_globex | applied | 2026-07-27 | — |

## Events
"""

TOKEN = "test-token"


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "applications.md")
        jobtrack.write_log_atomic(self.path, LOG)

        self.server = jobtrack_web.build_server(self.path, 0, TOKEN)
        self.port = self.server.server_address[1]
        self.origin = "http://127.0.0.1:{}".format(self.port)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        with urllib.request.urlopen(self.origin + path) as response:
            return response.status, response.read().decode("utf-8")

    def post(self, path, payload, token=TOKEN, origin=None):
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.origin + path, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header("X-Jobtrack-Token", token)
        if origin is not None:
            request.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")


class TestBinding(WebTestCase):
    def test_binds_to_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


class TestReads(WebTestCase):
    def test_index_returns_html(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("<table", body.lower())

    def test_page_has_no_external_asset_urls(self):
        _, body = self.get("/")
        for marker in ("http://", "https://", "//cdn"):
            self.assertNotIn(marker, body.replace(self.origin, ""))

    def test_api_returns_rows_stale_and_stats(self):
        status, body = self.get("/api/applications")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(len(payload["applications"]), 2)
        self.assertIn("stale", payload)
        self.assertIn("stats", payload)
        self.assertEqual(payload["statuses"], list(jobtrack.STATUSES))

    def test_api_reflects_edits_made_on_disk(self):
        jobtrack.write_log_atomic(
            self.path, jobtrack.set_status(LOG, "Northwind", "Project Manager", "screening")
        )
        _, body = self.get("/api/applications")
        rows = json.loads(body)["applications"]
        self.assertEqual(rows[0]["status"], "screening")

    def test_unknown_path_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.code, 404)


class TestWrites(WebTestCase):
    def test_post_status_updates_the_file(self):
        status, _ = self.post(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": "screening"},
        )
        self.assertEqual(status, 200)
        apps = jobtrack.parse_log(jobtrack.read_log(self.path))
        self.assertEqual(apps[0].status, "screening")

    def test_post_status_leaves_other_rows_byte_identical(self):
        self.post(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": "screening"},
        )
        after = jobtrack.read_log(self.path)
        self.assertEqual(
            [l for l in LOG.splitlines() if "Northwind" not in l],
            [l for l in after.splitlines() if "Northwind" not in l],
        )

    def test_post_event_appends_and_moves_last_contact(self):
        status, _ = self.post(
            "/api/event",
            {
                "company": "Globex",
                "role": "Product Manager",
                "date": "2026-08-02",
                "text": "phone screen booked",
            },
        )
        self.assertEqual(status, 200)
        globex = jobtrack.parse_log(jobtrack.read_log(self.path))[1]
        self.assertEqual(len(globex.events), 1)
        self.assertEqual(globex.last_contact, jobtrack.datetime.date(2026, 8, 2))
        self.assertEqual(globex.status, "applied")

    def test_invalid_status_returns_400(self):
        status, _ = self.post(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": "pending"},
        )
        self.assertEqual(status, 400)

    def test_unknown_row_returns_404(self):
        status, _ = self.post(
            "/api/status", {"company": "Nobody", "role": "Nothing", "status": "applied"}
        )
        self.assertEqual(status, 404)

    def test_missing_token_returns_403(self):
        status, _ = self.post(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": "screening"},
            token=None,
        )
        self.assertEqual(status, 403)

    def test_wrong_token_returns_403(self):
        status, _ = self.post(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": "screening"},
            token="bogus",
        )
        self.assertEqual(status, 403)

    def test_foreign_origin_returns_403(self):
        status, _ = self.post(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": "screening"},
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)

    def test_rejected_write_leaves_the_file_untouched(self):
        before = jobtrack.read_log(self.path)
        self.post(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": "screening"},
            token="bogus",
        )
        self.assertEqual(jobtrack.read_log(self.path), before)


class TestMalformedBody(WebTestCase):
    """A syntactically valid JSON body whose top level isn't an object must
    still get a clean 400, not a dropped connection (payload.get(...) would
    raise AttributeError on a list/str/None top level otherwise)."""

    def _assert_rejected(self, path, body_obj):
        before = jobtrack.read_log(self.path)
        status, _ = self.post(path, body_obj)
        self.assertEqual(status, 400)
        self.assertEqual(jobtrack.read_log(self.path), before)

    def test_status_rejects_bare_array(self):
        self._assert_rejected("/api/status", [1, 2, 3])

    def test_status_rejects_bare_string(self):
        self._assert_rejected("/api/status", "not an object")

    def test_status_rejects_null(self):
        self._assert_rejected("/api/status", None)

    def test_event_rejects_bare_array(self):
        self._assert_rejected("/api/event", [1, 2, 3])

    def test_event_rejects_bare_string(self):
        self._assert_rejected("/api/event", "not an object")

    def test_event_rejects_null(self):
        self._assert_rejected("/api/event", None)


class TestNonStringFields(WebTestCase):
    """JSON values can be any type. A non-string company used to reach
    jobtrack's '|' in value check and raise TypeError, which nothing caught."""

    def _assert_rejected(self, path, body_obj):
        before = jobtrack.read_log(self.path)
        status, body = self.post(path, body_obj)
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))
        self.assertEqual(jobtrack.read_log(self.path), before)

    def test_numeric_company_returns_400(self):
        self._assert_rejected(
            "/api/status", {"company": 123, "role": "Project Manager", "status": "ack"}
        )

    def test_numeric_role_returns_400(self):
        self._assert_rejected("/api/status", {"company": "Northwind", "role": 7, "status": "ack"})

    def test_non_string_status_returns_400(self):
        self._assert_rejected(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": ["ack"]},
        )

    def test_non_string_event_text_returns_400(self):
        self._assert_rejected(
            "/api/event",
            {
                "company": "Northwind", "role": "Project Manager",
                "date": "2026-08-02", "text": {"note": "hi"},
            },
        )

    def test_non_string_event_date_returns_400(self):
        self._assert_rejected(
            "/api/event",
            {"company": "Northwind", "role": "Project Manager", "date": 20260802, "text": "hi"},
        )

    def test_error_body_names_the_offending_field(self):
        _, body = self.post(
            "/api/status", {"company": 123, "role": "Project Manager", "status": "ack"}
        )
        self.assertIn("company", json.loads(body)["error"])


class TestEmptyEventText(WebTestCase):
    def test_empty_event_text_returns_400_without_writing(self):
        before = jobtrack.read_log(self.path)
        status, body = self.post(
            "/api/event",
            {"company": "Northwind", "role": "Project Manager", "date": "2026-08-02", "text": ""},
        )
        self.assertEqual(status, 400)
        self.assertIn("empty", json.loads(body)["error"])
        self.assertEqual(jobtrack.read_log(self.path), before)

    def test_whitespace_only_event_text_returns_400_without_writing(self):
        before = jobtrack.read_log(self.path)
        status, _ = self.post(
            "/api/event",
            {"company": "Northwind", "role": "Project Manager", "date": "2026-08-02", "text": "   "},
        )
        self.assertEqual(status, 400)
        self.assertEqual(jobtrack.read_log(self.path), before)

    def test_missing_event_text_returns_400_without_writing(self):
        before = jobtrack.read_log(self.path)
        status, _ = self.post(
            "/api/event", {"company": "Northwind", "role": "Project Manager", "date": "2026-08-02"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(jobtrack.read_log(self.path), before)

    def test_newline_in_event_text_returns_400_without_writing(self):
        before = jobtrack.read_log(self.path)
        status, _ = self.post(
            "/api/event",
            {
                "company": "Northwind", "role": "Project Manager",
                "date": "2026-08-02", "text": "a\n### Bogus — Role",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(jobtrack.read_log(self.path), before)


class TestLoopbackOrigins(WebTestCase):
    """The server binds 127.0.0.1 but the browser echoes the hostname the user
    typed. Opening the page on localhost used to serve a working page whose
    every write was refused as an invalid token."""

    def _post_from(self, origin):
        return self.post(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": "screening"},
            origin=origin,
        )

    def test_localhost_origin_is_accepted(self):
        status, _ = self._post_from("http://localhost:{}".format(self.port))
        self.assertEqual(status, 200)
        apps = jobtrack.parse_log(jobtrack.read_log(self.path))
        self.assertEqual(apps[0].status, "screening")

    def test_ipv6_loopback_origin_is_accepted(self):
        status, _ = self._post_from("http://[::1]:{}".format(self.port))
        self.assertEqual(status, 200)

    def test_ipv4_loopback_origin_is_still_accepted(self):
        status, _ = self._post_from("http://127.0.0.1:{}".format(self.port))
        self.assertEqual(status, 200)

    def test_loopback_host_on_another_port_is_rejected(self):
        status, _ = self._post_from("http://localhost:{}".format(self.port + 1))
        self.assertEqual(status, 403)

    def test_origin_rejection_does_not_blame_the_token(self):
        _, body = self._post_from("https://evil.example")
        message = json.loads(body)["error"]
        self.assertIn("origin", message.lower())
        self.assertNotIn("token", message.lower())

    def test_token_rejection_still_blames_the_token(self):
        status, body = self.post(
            "/api/status",
            {"company": "Northwind", "role": "Project Manager", "status": "screening"},
            token="bogus",
        )
        self.assertEqual(status, 403)
        self.assertIn("token", json.loads(body)["error"].lower())


@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores file permissions")
class TestUnreadableFile(WebTestCase):
    """An OSError from read/write/build_payload must become a 500 with a JSON
    body, not an escaped exception that drops the connection."""

    def setUp(self):
        super().setUp()
        os.chmod(self.path, 0o000)
        self.addCleanup(os.chmod, self.path, 0o600)

    def test_get_returns_500_not_a_dropped_connection(self):
        try:
            status, body = self.get("/api/applications")
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read().decode("utf-8")
        self.assertEqual(status, 500)
        self.assertIn("error", json.loads(body))

    def test_post_status_returns_500_not_a_dropped_connection(self):
        status, body = self.post(
            "/api/status", {"company": "Northwind", "role": "Project Manager", "status": "ack"}
        )
        self.assertEqual(status, 500)
        self.assertIn("error", json.loads(body))

    def test_post_event_returns_500_not_a_dropped_connection(self):
        status, body = self.post(
            "/api/event",
            {"company": "Northwind", "role": "Project Manager", "date": "2026-08-02", "text": "hi"},
        )
        self.assertEqual(status, 500)
        self.assertIn("error", json.loads(body))


class TestMissingFile(WebTestCase):
    def setUp(self):
        super().setUp()
        os.unlink(self.path)

    def test_get_reports_the_missing_file_cleanly(self):
        try:
            status, body = self.get("/api/applications")
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read().decode("utf-8")
        self.assertEqual(status, 500)
        self.assertIn("not initialised", json.loads(body)["error"])

    def test_post_reports_the_missing_file_cleanly(self):
        status, body = self.post(
            "/api/status", {"company": "Northwind", "role": "Project Manager", "status": "ack"}
        )
        self.assertEqual(status, 500)
        self.assertIn("not initialised", json.loads(body)["error"])


class TestUnparseableFile(WebTestCase):
    def test_post_against_a_broken_file_returns_a_response_not_a_drop(self):
        jobtrack.write_log_atomic(
            self.path,
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | Project Manager | nope | cv_northwind | applied | 2026-07-24 | — |\n",
        )
        status, body = self.post(
            "/api/status", {"company": "Northwind", "role": "Project Manager", "status": "ack"}
        )
        self.assertIn(status, (400, 500))
        self.assertIn("error", json.loads(body))


class TestPageContent(WebTestCase):
    def test_page_shows_needs_attention_and_stats_regions(self):
        _, body = self.get("/")
        self.assertIn('id="attention"', body)
        self.assertIn('id="stats"', body)

    def test_page_embeds_the_token(self):
        _, body = self.get("/")
        self.assertIn(TOKEN, body)

    def test_page_offers_every_status_in_the_dropdown_template(self):
        _, body = self.get("/")
        for status in jobtrack.STATUSES:
            self.assertIn(status, body)

    def test_page_supports_dark_mode(self):
        _, body = self.get("/")
        self.assertIn("prefers-color-scheme", body)

    def test_page_has_no_inline_event_handler_attributes(self):
        _, body = self.get("/")
        self.assertNotIn("onclick=", body.lower())

    def test_page_renders_the_by_cv_variant_breakdown(self):
        _, body = self.get("/")
        self.assertIn("by_cv", body)
        self.assertIn("by CV variant", body)
        self.assertIn(".bycv", body)

    def test_api_payload_carries_the_by_cv_data_the_page_renders(self):
        _, body = self.get("/api/applications")
        by_cv = json.loads(body)["stats"]["by_cv"]
        self.assertEqual(set(by_cv), {"cv_northwind", "cv_globex"})
        self.assertEqual(by_cv["cv_northwind"], {"responded": 0, "total": 1})


WATCHLIST = """# Watchlist

- include: strategy
- exclude: intern

| Company | Source | Keywords | Location | Added |
|---|---|---|---|---|
| Umbrella | ashby:umbrella | — | — | 2026-08-04 |
"""


class TestWatchlistApi(WebTestCase):
    def setUp(self):
        super().setUp()
        self.wl_path = os.path.join(self.tmp.name, "watchlist.md")
        jobtrack.write_log_atomic(self.wl_path, WATCHLIST)
        self.server.RequestHandlerClass.watchlist_path = self.wl_path
        self.server.RequestHandlerClass.state_path = os.path.join(self.tmp.name, "state.json")

    def test_get_watchlist_returns_rows_and_defaults(self):
        status, body = self.get("/api/watchlist")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(len(payload["watches"]), 1)
        self.assertEqual(payload["watches"][0]["company"], "Umbrella")
        self.assertIn("defaults", payload)

    def test_add_with_unresolvable_url_returns_400_with_the_reason(self):
        # resolve_url is mocked here rather than hitting example.com for real:
        # no test in this suite may touch the network, and the resolver's own
        # message (verbatim) is exactly what this test is checking gets
        # surfaced, regardless of how resolve_url reached it.
        message = (
            "could not identify a job board behind https://example.com/careers"
            " — supported boards are greenhouse, lever, ashby, smartrecruiters"
        )
        with mock.patch.object(
            jobtrack_watch, "resolve_url", side_effect=jobtrack_watch.ResolveError(message)
        ):
            status, body = self.post("/api/watchlist/add", {"url": "https://example.com/careers"})
        self.assertEqual(status, 400)
        self.assertIn("could not identify", json.loads(body)["error"].lower())

    def test_add_missing_url_returns_400(self):
        self.assertEqual(self.post("/api/watchlist/add", {})[0], 400)

    def test_remove_deletes_the_row(self):
        status, _ = self.post("/api/watchlist/remove", {"company": "Umbrella"})
        self.assertEqual(status, 200)
        self.assertEqual(
            len(jobtrack_watch.parse_watchlist(jobtrack.read_log(self.wl_path)).watches), 0
        )

    def test_remove_unknown_company_returns_404(self):
        self.assertEqual(self.post("/api/watchlist/remove", {"company": "Nobody"})[0], 404)

    def test_watchlist_writes_require_the_token(self):
        self.assertEqual(
            self.post("/api/watchlist/remove", {"company": "Umbrella"}, token=None)[0], 403
        )

    def test_watchlist_writes_reject_a_foreign_origin(self):
        self.assertEqual(
            self.post("/api/watchlist/remove", {"company": "Umbrella"},
                      origin="https://evil.example")[0], 403,
        )

    def test_rejected_write_leaves_the_watchlist_untouched(self):
        before = jobtrack.read_log(self.wl_path)
        self.post("/api/watchlist/remove", {"company": "Umbrella"}, token="bogus")
        self.assertEqual(jobtrack.read_log(self.wl_path), before)


class TestWatchlistPage(WebTestCase):
    def test_page_has_a_watchlist_section(self):
        _, body = self.get("/")
        self.assertIn('id="watchlist"', body)

    def test_page_still_has_no_external_assets(self):
        _, body = self.get("/")
        for marker in ("http://", "https://", "//cdn"):
            self.assertNotIn(marker, body.replace(self.origin, ""))

    def test_page_still_has_no_inline_handlers(self):
        _, body = self.get("/")
        self.assertNotIn("onclick=", body.lower())


class TestWatchlistAddPartialSuccess(WebTestCase):
    """A state.json write failure after the watchlist row is already written
    must not be reported as though the add failed -- see the task-8 review
    finding: the company is genuinely on the watchlist by that point, and a
    client that's told otherwise retries and gets a spurious 400 duplicate."""

    def setUp(self):
        super().setUp()
        self.wl_path = os.path.join(self.tmp.name, "watchlist.md")
        jobtrack.write_log_atomic(self.wl_path, WATCHLIST)
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.server.RequestHandlerClass.watchlist_path = self.wl_path
        self.server.RequestHandlerClass.state_path = self.state_path
        self.new_source = jobtrack_watch.Source(vendor="ashby", slug="newco")
        self.postings = [
            jobtrack_watch.Posting(id="1", title="Engineer", url="https://x/1", location="", posted=None)
        ]

    def _post_add_with_failing_state(self, url="https://jobs.ashbyhq.com/newco"):
        with mock.patch.object(jobtrack_watch, "resolve_url", return_value=self.new_source), \
             mock.patch.object(jobtrack_watch, "fetch_postings", return_value=self.postings), \
             mock.patch.object(jobtrack_watch, "save_state", side_effect=OSError("no space left on device")):
            return self.post("/api/watchlist/add", {"url": url})

    def test_state_write_failure_is_not_reported_as_a_failed_add(self):
        status, body = self._post_add_with_failing_state()
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertNotIn("error", payload)
        self.assertIn("warning", payload)

    def test_warning_names_the_state_file_not_the_watchlist_file(self):
        _, body = self._post_add_with_failing_state()
        warning = json.loads(body)["warning"]
        self.assertIn(self.state_path, warning)
        self.assertNotIn(self.wl_path, warning)

    def test_company_is_present_in_a_following_get(self):
        self._post_add_with_failing_state()
        _, body = self.get("/api/watchlist")
        companies = [watch["company"] for watch in json.loads(body)["watches"]]
        self.assertIn("newco", companies)

    def test_watchlist_write_failure_before_state_work_returns_a_genuine_error(self):
        """The inverse case: the watchlist.md write itself fails, before any
        state work has happened. This must still be a real error naming the
        watchlist file (not the state file), and nothing must be written."""
        real_write = jobtrack.write_log_atomic

        def flaky_write(path, text):
            if path == self.wl_path:
                raise OSError("disk full")
            return real_write(path, text)

        with mock.patch.object(
            jobtrack_watch, "resolve_url",
            return_value=jobtrack_watch.Source(vendor="ashby", slug="other"),
        ), mock.patch.object(jobtrack_watch, "fetch_postings", return_value=[]), mock.patch.object(
            jobtrack, "write_log_atomic", side_effect=flaky_write
        ):
            status, body = self.post("/api/watchlist/add", {"url": "https://jobs.ashbyhq.com/other"})

        self.assertEqual(status, 500)
        message = json.loads(body)["error"]
        self.assertIn(self.wl_path, message)
        self.assertNotIn(self.state_path, message)
        self.assertNotIn("other", jobtrack.read_log(self.wl_path))


# Minimal enough DOM to actually execute the page's <script> under node --
# not a browser shim, just enough for el()/getElementById/addEventListener
# to behave, so this exercises the real click-handler code path rather than
# asserting on the JS source text. __BASE__ is replaced with the running
# test server's origin so the script's relative fetch("/api/...") calls
# reach it for real, over loopback, with the resolver/fetch/save_state
# mocks active on the Python side for the duration of the subprocess call.
_NODE_HARNESS_PREAMBLE = """
'use strict';
function makeNode(tag) {
  const node = {
    tagName: tag,
    children: [],
    _listeners: {},
    // Real <input> elements default .value to "" even unset; this stub
    // isn't a real DOM, so without this an unfilled optional field (company,
    // keywords) throws on the page script's own `field.value.trim()`.
    value: "",
    append(...kids) { this.children.push(...kids); },
    addEventListener(type, handler) { this._listeners[type] = handler; },
  };
  let text = "";
  Object.defineProperty(node, "textContent", {
    get() { return text; },
    set(value) { text = value; if (value === "") node.children = []; },
  });
  return node;
}
const __registry = {};
global.document = {
  createElement: (tag) => makeNode(tag),
  createTextNode: (value) => ({ textContent: value }),
  getElementById(id) {
    if (!__registry[id]) __registry[id] = makeNode("div");
    return __registry[id];
  },
};
const __BASE = "__BASE__";
const __realFetch = global.fetch;
global.fetch = (path, opts) => __realFetch(__BASE + path, opts);
"""

_NODE_HARNESS_POSTAMBLE = """

function findAddForm() {
  const host = __registry["watchlist"];
  if (!host) return null;
  for (const child of host.children) {
    if (child.className === "wl-add") return child;
  }
  return null;
}

(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  await sleep(1000);

  const form = findAddForm();
  if (!form) { console.log(JSON.stringify({ fatal: "no add form found" })); process.exit(1); }
  const [urlInput, , , button] = form.children;
  const originalLabel = button.textContent;

  urlInput.value = "https://jobs.ashbyhq.com/newco";
  button._listeners.click();

  // Read the pending state synchronously, before the network round trip
  // resolves -- the whole point of the pending-state requirement.
  const pendingLabel = button.textContent;
  const pendingDisabled = button.disabled;

  await sleep(3000);

  const text = (id) => {
    const node = __registry[id];
    return (node && node.textContent) || "";
  };
  console.log(JSON.stringify({
    pendingLabelWasResolving: pendingLabel === "Resolving\\u2026",
    pendingDisabled: pendingDisabled === true,
    buttonReenabled: button.disabled === false,
    buttonLabelRestored: button.textContent === originalLabel,
    // Two separate slots: "wlerror" sits next to the add form, "error" at the
    // very bottom of the page. Which one a message lands in is the whole
    // point -- a message in "error" alone is one the user never sees.
    watchErrorText: text("wlerror"),
    pageErrorText: text("error"),
    urlValueLeftFilled: !!urlInput.value,
  }));
  process.exit(0);
})().catch((error) => {
  console.log(JSON.stringify({ fatal: String((error && error.stack) || error) }));
  process.exit(1);
});
"""


@unittest.skipUnless(shutil.which("node"), "node not available")
class NodePageTestCase(WebTestCase):
    """Runs the served page's real script under node (DOM-lite shim, real
    loopback fetch to the real test server), clicks Add, and reports what
    the page ended up in."""

    def run_add_click(self, patches):
        self.wl_path = os.path.join(self.tmp.name, "watchlist.md")
        jobtrack.write_log_atomic(self.wl_path, WATCHLIST)
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.server.RequestHandlerClass.watchlist_path = self.wl_path
        self.server.RequestHandlerClass.state_path = self.state_path

        _, page = self.get("/")
        match = re.search(r"<script>(.*)</script>", page, re.S)
        self.assertIsNotNone(match)
        script = match.group(1)

        harness = (
            _NODE_HARNESS_PREAMBLE.replace("__BASE__", self.origin) + script + _NODE_HARNESS_POSTAMBLE
        )
        harness_path = os.path.join(self.tmp.name, "harness.js")
        with open(harness_path, "w", encoding="utf-8") as handle:
            handle.write(harness)

        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            result = subprocess.run(
                ["node", harness_path], capture_output=True, text=True, timeout=20
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = [line for line in result.stdout.strip().splitlines() if line]
        outcome = json.loads(lines[-1])
        self.assertNotIn("fatal", outcome, outcome)
        return outcome


class TestWatchlistAddButtonRecovers(NodePageTestCase):
    """Confirms the add button and form genuinely recover after a
    partial-success (warning) response, rather than trusting that a full
    section re-render happens to make it look that way."""

    def test_form_clears_and_button_reenables_after_a_warning_response(self):
        source = jobtrack_watch.Source(vendor="ashby", slug="newco")
        postings = [
            jobtrack_watch.Posting(id="1", title="Engineer", url="https://x/1", location="", posted=None)
        ]
        outcome = self.run_add_click([
            mock.patch.object(jobtrack_watch, "resolve_url", return_value=source),
            mock.patch.object(jobtrack_watch, "fetch_postings", return_value=postings),
            mock.patch.object(
                jobtrack_watch, "save_state", side_effect=OSError("no space left on device")
            ),
        ])
        self.assertTrue(outcome["pendingLabelWasResolving"], outcome)
        self.assertTrue(outcome["pendingDisabled"], outcome)
        self.assertTrue(outcome["buttonReenabled"], outcome)
        self.assertTrue(outcome["buttonLabelRestored"], outcome)
        self.assertIn("no space left on device", outcome["watchErrorText"])
        self.assertFalse(outcome["urlValueLeftFilled"], outcome)


class TestWatchlistAddRejectionIsVisible(NodePageTestCase):
    """A rejected add must report why, next to the button that was clicked.

    The page-level "error" slot is the last element in <main>, below the whole
    applications table -- a rejection reported only there scrolls thousands of
    pixels out of view, and the add reads as having silently done nothing.
    """

    def test_resolve_rejection_lands_in_the_watchlist_slot(self):
        outcome = self.run_add_click([
            mock.patch.object(
                jobtrack_watch,
                "resolve_url",
                side_effect=jobtrack_watch.ResolveError(
                    "example.com runs Workday, which has no adapter yet"
                ),
            ),
        ])
        self.assertIn("no adapter yet", outcome["watchErrorText"], outcome)
        self.assertEqual(outcome["pageErrorText"], "", outcome)

    def test_rejected_add_leaves_the_url_filled_and_the_button_usable(self):
        outcome = self.run_add_click([
            mock.patch.object(
                jobtrack_watch,
                "resolve_url",
                side_effect=jobtrack_watch.ResolveError("could not identify a job board"),
            ),
        ])
        self.assertTrue(outcome["buttonReenabled"], outcome)
        self.assertTrue(outcome["buttonLabelRestored"], outcome)
        self.assertTrue(outcome["urlValueLeftFilled"], outcome)


if __name__ == "__main__":
    unittest.main()
