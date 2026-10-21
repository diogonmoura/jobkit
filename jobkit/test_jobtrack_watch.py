from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import jobtrack
import jobtrack_watch

# These tests predate jobkit.toml and exercise Telegram sends; jobkit's default
# channel is the console. Tests about the channel itself patch load() again.
_TELEGRAM = mock.patch.object(
    jobtrack_watch.jobkit_config, "load",
    return_value=jobtrack_watch.jobkit_config.Config(notify_channel="telegram"),
)


def setUpModule():
    _TELEGRAM.start()


def tearDownModule():
    _TELEGRAM.stop()


GOOD = """# Watchlist

**Default filters** — apply to every company unless overridden.
- include: strategy, product manager, transformation
- exclude: intern, engineer

| Company | Source | Keywords | Location | Added |
|---|---|---|---|---|
| Umbrella | ashby:umbrella | — | — | 2026-08-04 |
| Remote | greenhouse:remotecom | strategy, product | — | 2026-08-04 |
"""


class TestParseWatchlist(unittest.TestCase):
    def test_parses_default_filters(self):
        wl = jobtrack_watch.parse_watchlist(GOOD)
        self.assertEqual(wl.defaults.include, ["strategy", "product manager", "transformation"])
        self.assertEqual(wl.defaults.exclude, ["intern", "engineer"])

    def test_parses_rows_in_order(self):
        wl = jobtrack_watch.parse_watchlist(GOOD)
        self.assertEqual([w.company for w in wl.watches], ["Umbrella", "Remote"])

    def test_parses_source_into_vendor_and_slug(self):
        wl = jobtrack_watch.parse_watchlist(GOOD)
        self.assertEqual(wl.watches[0].source.vendor, "ashby")
        self.assertEqual(wl.watches[0].source.slug, "umbrella")

    def test_placeholder_keywords_means_none(self):
        self.assertIsNone(jobtrack_watch.parse_watchlist(GOOD).watches[0].keywords)

    def test_keyword_override_is_split(self):
        self.assertEqual(jobtrack_watch.parse_watchlist(GOOD).watches[1].keywords, ["strategy", "product"])

    def test_parses_added_date(self):
        self.assertEqual(
            jobtrack_watch.parse_watchlist(GOOD).watches[0].added, datetime.date(2026, 8, 4)
        )

    def test_empty_watchlist_parses_to_no_watches(self):
        text = (
            "# Watchlist\n\n- include: strategy\n- exclude: intern\n\n"
            "| Company | Source | Keywords | Location | Added |\n|---|---|---|---|---|\n"
        )
        wl = jobtrack_watch.parse_watchlist(text)
        self.assertEqual(wl.watches, [])
        self.assertEqual(wl.defaults.include, ["strategy"])

    def test_unknown_vendor_is_a_parse_error(self):
        text = GOOD.replace("ashby:umbrella", "bogusvendor:umbrella")
        with self.assertRaises(jobtrack.ParseError):
            jobtrack_watch.parse_watchlist(text)

    def test_source_without_colon_is_a_parse_error(self):
        text = GOOD.replace("ashby:umbrella", "ashby")
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack_watch.parse_watchlist(text)
        self.assertIn("vendor:slug", str(ctx.exception))

    def test_empty_slug_is_a_parse_error(self):
        text = GOOD.replace("ashby:umbrella", "ashby:")
        with self.assertRaises(jobtrack.ParseError):
            jobtrack_watch.parse_watchlist(text)

    def test_bad_added_date_is_a_parse_error(self):
        text = GOOD.replace("2026-08-04 |", "04/08/2026 |", 1)
        with self.assertRaises(jobtrack.ParseError):
            jobtrack_watch.parse_watchlist(text)

    def test_duplicate_company_is_a_parse_error(self):
        text = GOOD + "| Umbrella | lever:umbrella | — | — | 2026-08-05 |\n"
        with self.assertRaises(jobtrack.ParseError):
            jobtrack_watch.parse_watchlist(text)

    def test_wrong_column_count_is_a_parse_error(self):
        text = GOOD + "| Broken | ashby:x |\n"
        with self.assertRaises(jobtrack.ParseError):
            jobtrack_watch.parse_watchlist(text)

    def test_missing_default_filter_lines_yields_empty_filters(self):
        text = "# Watchlist\n\n| Company | Source | Keywords | Location | Added |\n|---|---|---|---|---|\n"
        wl = jobtrack_watch.parse_watchlist(text)
        self.assertEqual(wl.defaults.include, [])
        self.assertEqual(wl.defaults.exclude, [])

    def test_bad_vendor_mentions_watchlist_md_not_applications_md(self):
        text = GOOD.replace("ashby:umbrella", "bogusvendor:umbrella")
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack_watch.parse_watchlist(text)
        self.assertIn("watchlist.md", str(ctx.exception))
        self.assertNotIn("applications.md", str(ctx.exception))

    def test_bad_vendor_line_number_is_correct(self):
        text = GOOD.replace("ashby:umbrella", "bogusvendor:umbrella")
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack_watch.parse_watchlist(text)
        self.assertEqual(ctx.exception.line_no, 9)

    def test_bad_added_date_mentions_watchlist_md_not_applications_md(self):
        text = GOOD.replace("2026-08-04 |", "04/08/2026 |", 1)
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack_watch.parse_watchlist(text)
        self.assertIn("watchlist.md", str(ctx.exception))
        self.assertNotIn("applications.md", str(ctx.exception))

    def test_bad_added_date_line_number_is_correct(self):
        text = GOOD.replace("2026-08-04 |", "04/08/2026 |", 1)
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack_watch.parse_watchlist(text)
        self.assertEqual(ctx.exception.line_no, 9)

    def test_placeholder_location_means_none(self):
        self.assertIsNone(jobtrack_watch.parse_watchlist(GOOD).watches[0].location)

    def test_location_override_is_split(self):
        text = GOOD.replace(
            "| Remote | greenhouse:remotecom | strategy, product | — | 2026-08-04 |",
            "| Remote | greenhouse:remotecom | strategy, product | Portugal, Spain | 2026-08-04 |",
        )
        self.assertEqual(
            jobtrack_watch.parse_watchlist(text).watches[1].location, ["Portugal", "Spain"]
        )

    def test_default_location_filter_is_parsed(self):
        text = GOOD.replace(
            "- exclude: intern, engineer\n",
            "- exclude: intern, engineer\n- location: Portugal\n",
        )
        self.assertEqual(jobtrack_watch.parse_watchlist(text).defaults.location, ["Portugal"])

    def test_missing_default_location_line_yields_empty_list(self):
        self.assertEqual(jobtrack_watch.parse_watchlist(GOOD).defaults.location, [])

    def test_default_location_placeholder_does_not_become_a_literal_filter_term(self):
        # Regression: `- location: —` in the real watchlist.md must mean "no
        # restriction," not a literal ['—'] filter that silently matches
        # nothing. Every literal test fixture bypasses this because none of
        # them exercise the real file — that's exactly how this slipped
        # through to a final review instead of a test.
        wl = jobtrack_watch.parse_watchlist(GOOD.replace(
            "- exclude: intern, engineer\n", "- exclude: intern, engineer\n- location: —\n"
        ))
        self.assertEqual(wl.defaults.location, [])


FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def _fixture_text(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return handle.read()


class TestAdapters(unittest.TestCase):
    def test_greenhouse_maps_fields(self):
        postings = jobtrack_watch.parse_greenhouse(_fixture("greenhouse.json"))
        self.assertTrue(postings)
        first = postings[0]
        self.assertIsInstance(first.id, str)  # source int must be coerced
        self.assertTrue(first.title)
        self.assertTrue(first.url.startswith("http"))
        self.assertTrue(first.location)

    def test_greenhouse_tolerates_missing_location(self):
        payload = {"jobs": [{"id": 1, "title": "X", "absolute_url": "http://e.x", "updated_at": "2026-01-01"}]}
        self.assertEqual(jobtrack_watch.parse_greenhouse(payload)[0].location, "")

    def test_lever_uses_text_as_title_and_nested_location(self):
        postings = jobtrack_watch.parse_lever(_fixture("lever.json"))
        self.assertTrue(postings)
        self.assertTrue(postings[0].title)
        self.assertTrue(postings[0].url.startswith("http"))
        self.assertTrue(postings[0].location)

    def test_lever_converts_epoch_millis_to_iso_date(self):
        payload = [{"id": "a", "text": "T", "hostedUrl": "http://e.x", "createdAt": 1553186035299,
                    "categories": {"location": "Arlington, TX"}}]
        self.assertEqual(jobtrack_watch.parse_lever(payload)[0].posted, "2019-03-21")

    def test_lever_tolerates_missing_categories(self):
        payload = [{"id": "a", "text": "T", "hostedUrl": "http://e.x"}]
        self.assertEqual(jobtrack_watch.parse_lever(payload)[0].location, "")

    def test_ashby_maps_fields(self):
        postings = jobtrack_watch.parse_ashby(_fixture("ashby.json"))
        self.assertTrue(postings)
        self.assertTrue(postings[0].url.startswith("http"))

    def test_smartrecruiters_constructs_a_human_url_not_the_api_ref(self):
        postings = jobtrack_watch.parse_smartrecruiters(_fixture("smartrecruiters.json"), "Visa")
        self.assertTrue(postings)
        self.assertTrue(postings[0].url.startswith("https://jobs.smartrecruiters.com/Visa/"))
        self.assertNotIn("api.smartrecruiters.com", postings[0].url)

    def test_smartrecruiters_uses_name_as_title(self):
        postings = jobtrack_watch.parse_smartrecruiters(_fixture("smartrecruiters.json"), "Visa")
        self.assertTrue(all(p.title for p in postings))

    def test_every_vendor_has_an_adapter(self):
        for vendor in jobtrack_watch.VENDORS:
            self.assertIn(vendor, jobtrack_watch.ADAPTERS)

    def test_ids_are_unique_within_a_payload(self):
        postings = jobtrack_watch.parse_greenhouse(_fixture("greenhouse.json"))
        self.assertEqual(len({p.id for p in postings}), len(postings))

    def test_lever_eu_reuses_the_lever_parser(self):
        self.assertIs(
            jobtrack_watch.ADAPTERS["lever-eu"]["parse"],
            jobtrack_watch.ADAPTERS["lever"]["parse"],
        )

    def test_lever_eu_hits_the_eu_region_api_host(self):
        self.assertIn("api.eu.lever.co", jobtrack_watch.ADAPTERS["lever-eu"]["url"])


class TestAdapterHardening(unittest.TestCase):
    """A malformed *field* on an otherwise-recognisable record must degrade to
    an empty value, not throw -- otherwise one bad record on a board would
    discard every good one behind it. A record with no usable id must be
    skipped rather than emitted as a phantom (id="" would pollute the
    seen-id set). Top-level container shape mismatches are a different
    matter entirely -- see TestContainerShapeValidation."""

    def test_parse_greenhouse_degrades_wrong_typed_location_without_discarding_record(self):
        payload = {
            "jobs": [
                {"id": 1, "title": "Bad", "absolute_url": "http://e.x/1", "location": "a string"},
                {"id": 2, "title": "Good", "absolute_url": "http://e.x/2", "location": {"name": "NYC"}},
            ]
        }
        postings = jobtrack_watch.parse_greenhouse(payload)
        self.assertEqual(len(postings), 2)
        self.assertEqual(postings[0].location, "")
        self.assertEqual(postings[1].location, "NYC")

    def test_parse_greenhouse_skips_record_with_no_usable_id(self):
        # A None entry has no id to recover -- it must be dropped, not
        # emitted as a Posting(id="") that would pollute the seen-id set.
        payload = {"jobs": [None, {"id": 2, "title": "Good", "absolute_url": "http://e.x/2"}]}
        postings = jobtrack_watch.parse_greenhouse(payload)
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0].id, "2")
        self.assertEqual(postings[0].title, "Good")

    def test_parse_ashby_skips_record_with_no_usable_id(self):
        payload = {
            "jobs": [
                None,
                {"id": "x", "title": "T", "jobUrl": "http://e.x", "location": "NYC"},
            ]
        }
        postings = jobtrack_watch.parse_ashby(payload)
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0].id, "x")
        self.assertEqual(postings[0].location, "NYC")

    def test_parse_smartrecruiters_degrades_wrong_typed_location(self):
        payload = {"content": [{"id": 1, "name": "Bad", "location": "a string"}]}
        postings = jobtrack_watch.parse_smartrecruiters(payload, "X")
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0].location, "")
        self.assertTrue(postings[0].url.startswith("https://jobs.smartrecruiters.com/X/"))

    def test_parse_smartrecruiters_skips_record_with_no_usable_id(self):
        payload = {"content": [{"name": "No id"}, {"id": 2, "name": "Good"}]}
        postings = jobtrack_watch.parse_smartrecruiters(payload, "X")
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0].id, "2")

    def test_parse_lever_skips_record_with_no_usable_id(self):
        payload = [{"text": "No id"}, {"id": "a", "text": "Good", "hostedUrl": "http://e.x"}]
        postings = jobtrack_watch.parse_lever(payload)
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0].id, "a")


class TestContainerShapeValidation(unittest.TestCase):
    """A top-level container that isn't the expected shape can't be told
    apart from a genuinely empty board unless it's loud: a poller that
    stamps last_ok and shows green for a source silently returning [] hides
    exactly the failure health-tracking exists to catch. So a wrong-typed
    container, or the container key missing outright, must raise
    AdapterError naming the vendor and what was expected. A real empty
    board -- an empty list where a list is expected -- is NOT an error."""

    def test_greenhouse_raises_for_non_dict_payload(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_greenhouse(["not", "a", "dict"])

    def test_greenhouse_raises_for_wrong_typed_jobs_key(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_greenhouse({"jobs": "not-a-list"})

    def test_greenhouse_raises_for_missing_jobs_key(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_greenhouse({"other": []})

    def test_greenhouse_empty_board_returns_empty_list_without_raising(self):
        self.assertEqual(jobtrack_watch.parse_greenhouse({"jobs": []}), [])

    def test_lever_raises_for_dict_instead_of_list(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_lever({"jobs": []})

    def test_lever_raises_for_top_level_none(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_lever(None)

    def test_lever_empty_board_returns_empty_list_without_raising(self):
        self.assertEqual(jobtrack_watch.parse_lever([]), [])

    def test_ashby_raises_for_missing_container(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_ashby({"apiVersion": 1})

    def test_ashby_raises_for_wrong_typed_jobs_key(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_ashby({"jobs": {}})

    def test_ashby_empty_board_returns_empty_list_without_raising(self):
        self.assertEqual(jobtrack_watch.parse_ashby({"jobs": []}), [])

    def test_smartrecruiters_raises_for_missing_container(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_smartrecruiters({"offset": 0}, "X")

    def test_smartrecruiters_raises_for_wrong_typed_content_key(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_smartrecruiters({"content": "nope"}, "X")

    def test_smartrecruiters_empty_board_returns_empty_list_without_raising(self):
        self.assertEqual(jobtrack_watch.parse_smartrecruiters({"content": []}, "X"), [])


class TestFetchPostingsGuard(unittest.TestCase):
    """fetch_postings must never let AttributeError/TypeError/KeyError from a
    parse step escape as-is -- callers (a later CLI task) catch AdapterError
    specifically, and a raw exception would surface as an unhandled
    traceback instead."""

    def test_wraps_unexpected_parse_exception_as_adapter_error(self):
        source = jobtrack_watch.Source("greenhouse", "acme")

        def boom(payload, slug):
            raise AttributeError("boom")

        patched_adapters = dict(jobtrack_watch.ADAPTERS)
        patched_adapters["greenhouse"] = dict(patched_adapters["greenhouse"])
        patched_adapters["greenhouse"]["parse"] = boom

        with mock.patch.dict(jobtrack_watch.ADAPTERS, patched_adapters, clear=True):
            with mock.patch.object(jobtrack_watch, "_get_json", return_value={"jobs": []}):
                with self.assertRaises(jobtrack_watch.AdapterError) as ctx:
                    jobtrack_watch.fetch_postings(source)

        self.assertIn("greenhouse:acme", str(ctx.exception))

    def test_propagates_timeout_to_get_json(self):
        captured = {}

        def fake_get_json(url, timeout=jobtrack_watch.DEFAULT_TIMEOUT):
            captured["timeout"] = timeout
            return {"jobs": []}

        with mock.patch.object(jobtrack_watch, "_get_json", side_effect=fake_get_json):
            jobtrack_watch.fetch_postings(jobtrack_watch.Source("ashby", "acme"), timeout=7)

        self.assertEqual(captured["timeout"], 7)

    def test_get_json_wraps_http_error_as_adapter_error(self):
        error = urllib.error.HTTPError("http://e.x", 404, "Not Found", None, None)
        with mock.patch.object(jobtrack_watch.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(jobtrack_watch.AdapterError):
                jobtrack_watch._get_json("http://e.x")

    def test_get_json_wraps_non_json_body_as_adapter_error(self):
        class FakeResponse:
            def read(self):
                return b"not json"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch.object(jobtrack_watch.urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaises(jobtrack_watch.AdapterError):
                jobtrack_watch._get_json("http://e.x")


class TestSmartRecruitersPagination(unittest.TestCase):
    def _source(self):
        return jobtrack_watch.Source("smartrecruiters", "acme")

    def test_terminates_when_total_found_zero(self):
        page = {"offset": 0, "limit": 100, "totalFound": 0, "content": []}
        with mock.patch.object(jobtrack_watch, "_get_json", return_value=page) as get_json:
            postings = jobtrack_watch.fetch_postings(self._source())
        self.assertEqual(postings, [])
        get_json.assert_called_once()

    def test_terminates_on_short_final_page(self):
        page1 = {"offset": 0, "limit": 1, "totalFound": 2, "content": [{"id": 1, "name": "A"}]}
        page2 = {"offset": 1, "limit": 1, "totalFound": 2, "content": [{"id": 2, "name": "B"}]}
        with mock.patch.object(jobtrack_watch, "_get_json", side_effect=[page1, page2]):
            postings = jobtrack_watch.fetch_postings(self._source())
        self.assertEqual([p.id for p in postings], ["1", "2"])

    def test_terminates_on_empty_page(self):
        page1 = {"offset": 0, "limit": 1, "totalFound": 5, "content": [{"id": 1, "name": "A"}]}
        page2 = {"offset": 1, "limit": 1, "totalFound": 5, "content": []}
        with mock.patch.object(jobtrack_watch, "_get_json", side_effect=[page1, page2]) as get_json:
            postings = jobtrack_watch.fetch_postings(self._source())
        self.assertEqual(len(postings), 1)
        self.assertEqual(get_json.call_count, 2)

    def test_terminates_with_missing_limit(self):
        page = {"offset": 0, "totalFound": 1, "content": [{"id": 1, "name": "A"}]}
        with mock.patch.object(jobtrack_watch, "_get_json", return_value=page) as get_json:
            postings = jobtrack_watch.fetch_postings(self._source())
        self.assertEqual(len(postings), 1)
        get_json.assert_called_once()

    def test_terminates_with_zero_limit(self):
        page = {"offset": 0, "limit": 0, "totalFound": 1, "content": [{"id": 1, "name": "A"}]}
        with mock.patch.object(jobtrack_watch, "_get_json", return_value=page) as get_json:
            postings = jobtrack_watch.fetch_postings(self._source())
        self.assertEqual(len(postings), 1)
        get_json.assert_called_once()

    def test_terminates_with_negative_limit(self):
        # Regression test for the infinite-loop bug: a negative limit used to
        # *decrement* offset every iteration, so both loop conditions
        # (offset < total, offset < SMARTRECRUITERS_MAX) stayed true forever.
        # totalFound=10 matches the reviewer's repro exactly.
        page = {"offset": 0, "limit": -5, "totalFound": 10, "content": [{"id": 1, "name": "A"}]}
        call_count = {"n": 0}

        def fake_get_json(url, timeout=jobtrack_watch.DEFAULT_TIMEOUT):
            call_count["n"] += 1
            if call_count["n"] > 5:
                raise AssertionError("pagination did not terminate")
            return page

        with mock.patch.object(jobtrack_watch, "_get_json", side_effect=fake_get_json):
            postings = jobtrack_watch.fetch_postings(self._source())
        self.assertEqual(len(postings), 1)
        self.assertEqual(call_count["n"], 1)


class TestSuccessFactorsAdapter(unittest.TestCase):
    def test_parses_a_single_page_of_rows(self):
        html = _fixture_text("successfactors_page1.html")
        parser = jobtrack_watch._SuccessFactorsRowParser("https://jobs.edp.com")
        parser.feed(html)
        self.assertEqual(len(parser.postings), 2)
        first = parser.postings[0]
        self.assertEqual(first.id, "1000000001")
        self.assertEqual(first.title, "Senior HR Business Partner")
        self.assertEqual(first.url, "https://jobs.edp.com/job/Lisbon-Senior-HR-Business-Partner/1000000001/")
        self.assertEqual(first.location, "Lisbon, PT")
        self.assertEqual(first.posted, "Aug 6, 2026")

    def test_dedupes_mobile_and_desktop_duplicate_markup(self):
        # Every field renders twice (mobile + desktop echo) with identical
        # text -- this must not produce two postings or concatenated text.
        html = _fixture_text("successfactors_page1.html")
        parser = jobtrack_watch._SuccessFactorsRowParser("https://jobs.edp.com")
        parser.feed(html)
        self.assertEqual(len(parser.postings), 2)
        self.assertEqual(parser.postings[0].location, "Lisbon, PT")  # not "Lisbon, PTLisbon, PT"

    def test_noresults_fallback_is_flagged(self):
        html = _fixture_text("successfactors_noresults.html")
        parser = jobtrack_watch._SuccessFactorsRowParser("https://jobs.edp.com")
        parser.feed(html)
        self.assertTrue(parser.saw_noresults)

    def test_fetch_returns_empty_when_noresults_flagged(self):
        # Even though the noresults fixture DOES contain a data-row, the
        # fallback trap must make fetch_postings report zero postings, not
        # the unrelated "most recent jobs" row.
        pages = {0: _fixture_text("successfactors_noresults.html")}
        with mock.patch.object(jobtrack_watch, "_get_html", side_effect=lambda url, timeout: pages[0]):
            postings = jobtrack_watch._fetch_successfactors_postings(
                jobtrack_watch.Source("successfactors", "jobs.edp.com"), timeout=5
            )
        self.assertEqual(postings, [])

    def test_fetch_paginates_across_pages(self):
        pages = [_fixture_text("successfactors_page1.html"), _fixture_text("successfactors_page2.html")]
        calls = []

        def fake_get_html(url, timeout):
            calls.append(url)
            return pages[len(calls) - 1]

        with mock.patch.object(jobtrack_watch, "_get_html", side_effect=fake_get_html):
            postings = jobtrack_watch._fetch_successfactors_postings(
                jobtrack_watch.Source("successfactors", "jobs.edp.com"), timeout=5
            )
        self.assertEqual([p.id for p in postings], ["1000000001", "1000000002", "1000000003"])
        self.assertEqual(len(calls), 2)
        self.assertIn("startrow=0", calls[0])
        self.assertIn("startrow=2", calls[1])
        self.assertNotIn("locationsearch", calls[0])  # never uses the vendor's own location query

    def test_fetch_stops_after_the_last_page(self):
        # page1 (2 of 3 total) + page2 (the final 1) must not trigger a third fetch.
        pages = [_fixture_text("successfactors_page1.html"), _fixture_text("successfactors_page2.html")]
        calls = []

        def fake_get_html(url, timeout):
            calls.append(url)
            return pages[len(calls) - 1]

        with mock.patch.object(jobtrack_watch, "_get_html", side_effect=fake_get_html):
            jobtrack_watch._fetch_successfactors_postings(
                jobtrack_watch.Source("successfactors", "jobs.edp.com"), timeout=5
            )
        self.assertEqual(len(calls), 2)

    def test_fetch_postings_dispatches_successfactors_to_the_html_path(self):
        with mock.patch.object(
            jobtrack_watch, "_fetch_successfactors_postings",
            return_value=[jobtrack_watch.Posting("1", "T", "https://e.x/1", "Lisbon, PT", None)],
        ) as fake:
            postings = jobtrack_watch.fetch_postings(jobtrack_watch.Source("successfactors", "jobs.edp.com"))
        fake.assert_called_once()
        self.assertEqual(len(postings), 1)


class TestSuccessFactorsRMK(unittest.TestCase):
    """Northwind's client-rendered RMK search: no data-row table, a JSON API instead."""

    def _source(self):
        return jobtrack_watch.Source("successfactors", "jobs.northwind.com")

    def _pages(self, *names):
        return [_fixture(name) for name in names]

    def test_parses_a_page_skipping_entries_without_an_id(self):
        postings = jobtrack_watch.parse_successfactors_rmk(
            _fixture("successfactors_rmk_page0.json"), "https://jobs.northwind.com"
        )
        self.assertEqual([p.id for p in postings], ["76657", "78939"])  # "Summer Intern" has no id
        first = postings[0]
        self.assertEqual(first.title, "Head of Global Business Services Hub Portugal (m/f/d)")
        self.assertEqual(
            first.url,
            "https://jobs.northwind.com/job/Head-of-Global-Business-Services-Hub-Portugal-%28mfd%29/76657-en_US/",
        )
        self.assertEqual(first.location, "Porto, PRT, 4150-196")  # <br/> stripped
        self.assertEqual(first.posted, "2026-03-06")

    def test_unescapes_html_entities_in_title_and_url(self):
        postings = jobtrack_watch.parse_successfactors_rmk(
            _fixture("successfactors_rmk_page0.json"), "https://jobs.northwind.com"
        )
        self.assertEqual(postings[1].title, "Sales & Service Engineer")
        self.assertEqual(postings[1].url, "https://jobs.northwind.com/job/Sales-&-Service-Engineer/78939-en_US/")

    def test_location_codes_and_multiple_locations(self):
        postings = jobtrack_watch.parse_successfactors_rmk(
            _fixture("successfactors_rmk_page1.json"), "https://jobs.northwind.com"
        )
        self.assertEqual(postings[0].location, "Appleton, USA, 54911; Remote")
        self.assertEqual(postings[1].location, "Global")

    def test_a_page_past_the_end_is_empty_not_an_error(self):
        self.assertEqual(
            jobtrack_watch.parse_successfactors_rmk(
                _fixture("successfactors_rmk_past_end.json"), "https://jobs.northwind.com"
            ),
            [],
        )

    def test_a_non_dict_payload_is_an_adapter_error(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_successfactors_rmk([], "https://jobs.northwind.com")

    def test_detects_the_rmk_fingerprint(self):
        self.assertTrue(jobtrack_watch._is_successfactors_rmk(_fixture_text("successfactors_rmk_search.html")))
        self.assertFalse(jobtrack_watch._is_successfactors_rmk(_fixture_text("successfactors_page1.html")))

    def test_fetch_falls_back_to_the_api_and_pages_until_total(self):
        pages = self._pages("successfactors_rmk_page0.json", "successfactors_rmk_page1.json")
        bodies = []

        def fake_post_json(url, body, timeout):
            bodies.append((url, body))
            return pages[body["pageNumber"]]

        with mock.patch.object(
            jobtrack_watch, "_get_html", return_value=_fixture_text("successfactors_rmk_search.html")
        ), mock.patch.object(jobtrack_watch, "_post_json", side_effect=fake_post_json):
            postings = jobtrack_watch.fetch_postings(self._source())

        self.assertEqual([p.id for p in postings], ["76657", "78939", "78566", "78034"])
        self.assertEqual(len(bodies), 2)  # 3 + 2 entries reach totalJobs=5, no third request
        self.assertEqual(bodies[0][0], "https://jobs.northwind.com/services/recruiting/v1/jobs")
        self.assertEqual([b["pageNumber"] for _, b in bodies], [0, 1])
        self.assertEqual(bodies[0][1]["sortBy"], "date")  # the default order is unstable across pages

    def test_fetch_stops_on_a_page_past_the_end(self):
        pages = self._pages("successfactors_rmk_page0.json", "successfactors_rmk_past_end.json")
        page0 = dict(pages[0], totalJobs=50)  # total overstates what the API will actually page out
        calls = []

        def fake_post_json(url, body, timeout):
            calls.append(body["pageNumber"])
            return page0 if body["pageNumber"] == 0 else pages[1]

        with mock.patch.object(
            jobtrack_watch, "_get_html", return_value=_fixture_text("successfactors_rmk_search.html")
        ), mock.patch.object(jobtrack_watch, "_post_json", side_effect=fake_post_json):
            postings = jobtrack_watch.fetch_postings(self._source())
        self.assertEqual([p.id for p in postings], ["76657", "78939"])
        self.assertEqual(calls, [0, 1])

    def test_fetch_dedupes_a_job_repeated_across_pages(self):
        page0 = _fixture("successfactors_rmk_page0.json")
        page1 = _fixture("successfactors_rmk_page1.json")
        page1["jobSearchResult"].append(page0["jobSearchResult"][1])
        page1["totalJobs"] = page0["totalJobs"] = 6
        with mock.patch.object(
            jobtrack_watch, "_get_html", return_value=_fixture_text("successfactors_rmk_search.html")
        ), mock.patch.object(
            jobtrack_watch, "_post_json",
            side_effect=lambda url, body, timeout: [page0, page1][body["pageNumber"]],
        ):
            postings = jobtrack_watch.fetch_postings(self._source())
        self.assertEqual([p.id for p in postings], ["76657", "78939", "78566", "78034"])

    def test_a_classic_board_never_calls_the_api(self):
        pages = [_fixture_text("successfactors_page1.html"), _fixture_text("successfactors_page2.html")]
        with mock.patch.object(jobtrack_watch, "_get_html", side_effect=pages), mock.patch.object(
            jobtrack_watch, "_post_json"
        ) as fake_post:
            jobtrack_watch.fetch_postings(jobtrack_watch.Source("successfactors", "jobs.edp.com"))
        fake_post.assert_not_called()

    def test_an_api_http_error_surfaces_as_adapter_error(self):
        def fake_urlopen(request, timeout=None):
            if request.full_url.endswith("/services/recruiting/v1/jobs"):
                raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, None)
            raise AssertionError("unexpected request")

        with mock.patch.object(
            jobtrack_watch, "_get_html", return_value=_fixture_text("successfactors_rmk_search.html")
        ), mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(jobtrack_watch.AdapterError):
                jobtrack_watch.fetch_postings(self._source())


class TestResolver(unittest.TestCase):
    def test_resolves_greenhouse_board_url(self):
        s = jobtrack_watch.resolve_url("https://boards.greenhouse.io/remotecom", fetch=False)
        self.assertEqual((s.vendor, s.slug), ("greenhouse", "remotecom"))

    def test_resolves_new_style_greenhouse_url(self):
        s = jobtrack_watch.resolve_url("https://job-boards.greenhouse.io/stripe/jobs/123", fetch=False)
        self.assertEqual((s.vendor, s.slug), ("greenhouse", "stripe"))

    def test_resolves_lever_url(self):
        s = jobtrack_watch.resolve_url("https://jobs.lever.co/leverdemo/abc-123", fetch=False)
        self.assertEqual((s.vendor, s.slug), ("lever", "leverdemo"))

    def test_resolves_ashby_url(self):
        s = jobtrack_watch.resolve_url("https://jobs.ashbyhq.com/umbrella", fetch=False)
        self.assertEqual((s.vendor, s.slug), ("ashby", "umbrella"))

    def test_resolves_smartrecruiters_url(self):
        s = jobtrack_watch.resolve_url("https://jobs.smartrecruiters.com/Visa/744000", fetch=False)
        self.assertEqual((s.vendor, s.slug), ("smartrecruiters", "Visa"))

    def test_ignores_scheme_and_www(self):
        s = jobtrack_watch.resolve_url("boards.greenhouse.io/remotecom", fetch=False)
        self.assertEqual(s.vendor, "greenhouse")

    def test_unresolvable_url_raises_naming_what_was_tried(self):
        with self.assertRaises(jobtrack_watch.ResolveError) as ctx:
            jobtrack_watch.resolve_url("https://example.com/careers", fetch=False)
        self.assertIn("could not identify", str(ctx.exception).lower())

    def test_unsupported_vendor_is_named_explicitly(self):
        html = '<a href="https://careers-acme.icims.com/jobs">Jobs</a>'
        with self.assertRaises(jobtrack_watch.ResolveError) as ctx:
            jobtrack_watch._resolve_from_html(html, "https://acme.com/careers")
        self.assertIn("icims", str(ctx.exception).lower())

    def test_resolves_successfactors_by_content_fingerprint(self):
        html = '<script src="https://career5.successfactors.eu/x.js"></script>'
        s = jobtrack_watch._resolve_from_html(html, "https://jobs.edp.com/search/")
        self.assertEqual(s.vendor, "successfactors")
        self.assertEqual(s.slug, "jobs.edp.com")

    def test_successfactors_slug_is_the_pasted_hostname_not_a_path_segment(self):
        # Unlike every host-pattern vendor, SF CSB customers run on their own
        # vanity domain -- there is no shared host to extract a slug from.
        html = '<link href="//rmkcdn.successfactors.com/x.css" />'
        s = jobtrack_watch._resolve_from_html(html, "https://careers.acmecorp.com/jobs/search/?q=")
        self.assertEqual((s.vendor, s.slug), ("successfactors", "careers.acmecorp.com"))

    def test_known_host_vendor_still_wins_over_content_fingerprint(self):
        # A page that (oddly) mentions successfactors AND embeds a real
        # Greenhouse link must still resolve to the known host -- host-pattern
        # detection runs first, content-fingerprint detection is the fallback.
        html = (
            '<script src="https://career5.successfactors.eu/x.js"></script>'
            '<a href="https://boards.greenhouse.io/acme">Jobs</a>'
        )
        s = jobtrack_watch._resolve_from_html(html, "https://acme.com/careers")
        self.assertEqual((s.vendor, s.slug), ("greenhouse", "acme"))

    def test_successfactors_no_longer_in_unsupported_hints(self):
        self.assertNotIn("successfactors", jobtrack_watch.UNSUPPORTED_HINTS)

    def test_resolves_supported_vendor_embedded_in_page_html(self):
        html = '<iframe src="https://boards.greenhouse.io/acmecorp"></iframe>'
        s = jobtrack_watch._resolve_from_html(html, "https://acme.com/careers")
        self.assertEqual((s.vendor, s.slug), ("greenhouse", "acmecorp"))

    def test_empty_url_raises(self):
        with self.assertRaises(jobtrack_watch.ResolveError):
            jobtrack_watch.resolve_url("   ", fetch=False)

    def test_phenomenal_does_not_raise_phenom_error(self):
        """A page with 'phenomenal' should not trigger Phenom detection."""
        html = '<h1>Join our phenomenal team!</h1>'
        with self.assertRaises(jobtrack_watch.ResolveError) as ctx:
            jobtrack_watch._resolve_from_html(html, "https://acme.com/careers")
        # Should get generic error, not mentioning Phenom
        self.assertNotIn("phenom", str(ctx.exception).lower())
        self.assertIn("could not identify", str(ctx.exception).lower())

    def test_genuine_phenom_page_raises_phenom_explicitly(self):
        """A page containing phenompeople should still raise Phenom error."""
        html = '<script src="https://career.phenompeople.com/x.js"></script>'
        with self.assertRaises(jobtrack_watch.ResolveError) as ctx:
            jobtrack_watch._resolve_from_html(html, "https://acme.com/careers")
        self.assertIn("phenom", str(ctx.exception).lower())

    def test_vendor_in_query_string_does_not_resolve(self):
        """A URL with vendor host in query string should not resolve to that vendor."""
        # Should not resolve to greenhouse despite having it in the query string
        with self.assertRaises(jobtrack_watch.ResolveError):
            jobtrack_watch.resolve_url(
                "https://evil.example.com/redirect?to=boards.greenhouse.io/attacker",
                fetch=False
            )

    def test_legitimate_url_with_query_string_resolves(self):
        """Query strings should not interfere with resolution."""
        s = jobtrack_watch.resolve_url("https://boards.greenhouse.io/remotecom?foo=bar", fetch=False)
        self.assertEqual((s.vendor, s.slug), ("greenhouse", "remotecom"))

    def test_legitimate_url_with_fragment_resolves(self):
        """URL fragments should not interfere with resolution."""
        s = jobtrack_watch.resolve_url("https://boards.greenhouse.io/remotecom#section", fetch=False)
        self.assertEqual((s.vendor, s.slug), ("greenhouse", "remotecom"))

    def test_legitimate_url_with_trailing_slash_resolves(self):
        """Trailing slashes should not interfere with resolution."""
        s = jobtrack_watch.resolve_url("https://boards.greenhouse.io/remotecom/", fetch=False)
        self.assertEqual((s.vendor, s.slug), ("greenhouse", "remotecom"))

    def test_ordering_trap_supported_first_then_unsupported(self):
        """Supported vendor link first, then unsupported reference — supported should win."""
        html = '<a href="https://boards.greenhouse.io/acme">Jobs</a><script src="https://acme.wd3.myworkdayjobs.com/x.js"></script>'
        s = jobtrack_watch._resolve_from_html(html, "https://acme.com/careers")
        self.assertEqual((s.vendor, s.slug), ("greenhouse", "acme"))

    def test_ordering_trap_unsupported_first_then_supported(self):
        """Unsupported reference first, then supported vendor link — supported should still win."""
        html = '<script src="https://acme.wd3.myworkdayjobs.com/x.js"></script><a href="https://boards.greenhouse.io/acme">Jobs</a>'
        s = jobtrack_watch._resolve_from_html(html, "https://acme.com/careers")
        self.assertEqual((s.vendor, s.slug), ("greenhouse", "acme"))

    def test_resolves_lever_eu_url(self):
        s = jobtrack_watch.resolve_url("https://jobs.eu.lever.co/olx/abc-123", fetch=False)
        self.assertEqual((s.vendor, s.slug), ("lever-eu", "olx"))

    def test_resolves_lever_eu_link_embedded_in_page_html(self):
        html = '<a href="https://jobs.eu.lever.co/olx/460655c1-7e11-49e8-b8a2-eca7c7ed3008">Apply</a>'
        s = jobtrack_watch._resolve_from_html(html, "https://careers.olxgroup.com/jobs/")
        self.assertEqual((s.vendor, s.slug), ("lever-eu", "olx"))

    def test_lever_eu_does_not_shadow_the_default_lever_host(self):
        # jobs.lever.co (no "eu.") must still resolve to plain "lever", not "lever-eu".
        s = jobtrack_watch.resolve_url("https://jobs.lever.co/leverdemo/abc-123", fetch=False)
        self.assertEqual(s.vendor, "lever")


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state.json")

    def test_missing_file_loads_empty(self):
        self.assertEqual(jobtrack_watch.load_state(self.path), {})

    def test_round_trips(self):
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["1", "2"], last_ok="2026-08-04")}
        jobtrack_watch.save_state(self.path, state)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(sorted(loaded["ashby:acme"].seen_ids), ["1", "2"])
        self.assertEqual(loaded["ashby:acme"].last_ok, "2026-08-04")

    def test_corrupt_file_rebuilds_empty_rather_than_raising(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json at all")
        self.assertEqual(jobtrack_watch.load_state(self.path), {})

    def test_state_is_keyed_by_source_not_company(self):
        watch = jobtrack_watch.Watch(
            company="Acme", source=jobtrack_watch.Source("ashby", "acme"),
            keywords=None, added=datetime.date(2026, 8, 4),
        )
        self.assertEqual(jobtrack_watch.state_key(watch), "ashby:acme")
        renamed = jobtrack_watch.Watch(
            company="Acme Corp", source=jobtrack_watch.Source("ashby", "acme"),
            keywords=None, added=datetime.date(2026, 8, 4),
        )
        self.assertEqual(jobtrack_watch.state_key(renamed), jobtrack_watch.state_key(watch))

    def test_save_is_atomic_leaving_no_temp_files(self):
        jobtrack_watch.save_state(self.path, {"a:b": jobtrack_watch.SourceState(seen_ids=["1"])})
        self.assertEqual(os.listdir(self.tmp.name), ["state.json"])

    def test_unknown_keys_in_stored_state_are_ignored(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"ashby:acme": {"seen_ids": ["1"], "bogus_field": 42}}, handle)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(loaded["ashby:acme"].seen_ids, ["1"])

    def test_consecutive_failures_coerces_non_numeric_string(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"a:b": {"seen_ids": [], "consecutive_failures": "abc"}}, handle)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(loaded["a:b"].consecutive_failures, 0)

    def test_consecutive_failures_coerces_list(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"a:b": {"seen_ids": [], "consecutive_failures": [1, 2]}}, handle)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(loaded["a:b"].consecutive_failures, 0)

    def test_consecutive_failures_coerces_dict(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"a:b": {"seen_ids": [], "consecutive_failures": {"x": 1}}}, handle)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(loaded["a:b"].consecutive_failures, 0)

    def test_consecutive_failures_coerces_null(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"a:b": {"seen_ids": [], "consecutive_failures": None}}, handle)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(loaded["a:b"].consecutive_failures, 0)

    def test_consecutive_failures_coerces_float(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"a:b": {"seen_ids": [], "consecutive_failures": 3.7}}, handle)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(loaded["a:b"].consecutive_failures, 3)

    def test_last_ok_non_string_becomes_none(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"a:b": {"seen_ids": [], "last_ok": 123}}, handle)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertIsNone(loaded["a:b"].last_ok)

    def test_last_error_non_string_becomes_none(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"a:b": {"seen_ids": [], "last_error": ["list"]}}, handle)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertIsNone(loaded["a:b"].last_error)

    def test_corrupt_file_emits_stderr_warning(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json at all")
        captured = io.StringIO()
        with mock.patch("sys.stderr", captured):
            loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(loaded, {})
        self.assertIn(self.path, captured.getvalue())
        self.assertIn("warning", captured.getvalue())

    def test_missing_file_does_not_emit_stderr(self):
        # Ensure path does not exist
        self.assertFalse(os.path.exists(self.path))
        captured = io.StringIO()
        with mock.patch("sys.stderr", captured):
            loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(loaded, {})
        self.assertEqual(captured.getvalue(), "")

    def test_mixed_bad_fields_loads_good_ones(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({
                "a:b": {
                    "seen_ids": ["1", "2"],
                    "last_ok": "2026-08-04",
                    "last_error": ["not", "a", "string"],
                    "consecutive_failures": "not-a-number",
                }
            }, handle)
        loaded = jobtrack_watch.load_state(self.path)
        self.assertEqual(loaded["a:b"].seen_ids, ["1", "2"])
        self.assertEqual(loaded["a:b"].last_ok, "2026-08-04")
        self.assertIsNone(loaded["a:b"].last_error)
        self.assertEqual(loaded["a:b"].consecutive_failures, 0)


def _watch(company="Acme", vendor="ashby", slug="acme", keywords=None, location=None):
    return jobtrack_watch.Watch(
        company=company, source=jobtrack_watch.Source(vendor, slug),
        keywords=keywords, added=datetime.date(2026, 8, 4), location=location,
    )


def _posting(pid, title, location="Porto"):
    return jobtrack_watch.Posting(id=pid, title=title, url="https://e.x/" + pid,
                                  location=location, posted="2026-08-01")


class TestMatching(unittest.TestCase):
    def test_empty_include_matches_everything(self):
        self.assertTrue(jobtrack_watch.matches("Anything At All", [], []))

    def test_include_requires_one_term(self):
        self.assertTrue(jobtrack_watch.matches("Head of Strategy", ["strategy"], []))
        self.assertFalse(jobtrack_watch.matches("Backend Engineer", ["strategy"], []))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(jobtrack_watch.matches("HEAD OF STRATEGY", ["strategy"], []))

    def test_exclude_beats_include(self):
        self.assertFalse(jobtrack_watch.matches("Strategy Intern", ["strategy"], ["intern"]))

    def test_exclude_applies_even_with_empty_include(self):
        self.assertFalse(jobtrack_watch.matches("Sales Lead", [], ["sales"]))

    def test_multi_word_include_term_matches_as_substring(self):
        self.assertTrue(jobtrack_watch.matches("Senior Product Manager", ["product manager"], []))
        self.assertFalse(jobtrack_watch.matches("Product Designer", ["product manager"], []))

    def test_per_company_keywords_override_defaults(self):
        defaults = jobtrack_watch.Filters(include=["strategy"], exclude=["intern"])
        f = jobtrack_watch.filters_for(_watch(keywords=["product"]), defaults)
        self.assertEqual(f.include, ["product"])
        self.assertEqual(f.exclude, ["intern"])  # excludes still inherited

    def test_no_override_uses_defaults(self):
        defaults = jobtrack_watch.Filters(include=["strategy"], exclude=["intern"])
        self.assertEqual(jobtrack_watch.filters_for(_watch(), defaults).include, ["strategy"])

    def test_filters_for_resolves_location_override(self):
        defaults = jobtrack_watch.Filters(include=[], exclude=[], location=["Poland"])
        f = jobtrack_watch.filters_for(_watch(location=["Portugal"]), defaults)
        self.assertEqual(f.location, ["Portugal"])

    def test_filters_for_falls_back_to_default_location(self):
        defaults = jobtrack_watch.Filters(include=[], exclude=[], location=["Poland"])
        self.assertEqual(jobtrack_watch.filters_for(_watch(), defaults).location, ["Poland"])

    def test_location_filter_requires_a_matching_term(self):
        self.assertTrue(jobtrack_watch.matches(
            "Head of Strategy", [], [], location="Lisbon, Portugal", location_filter=["Portugal"],
        ))
        self.assertFalse(jobtrack_watch.matches(
            "Head of Strategy", [], [], location="Warsaw, Poland", location_filter=["Portugal"],
        ))

    def test_location_filter_is_case_insensitive(self):
        self.assertTrue(jobtrack_watch.matches(
            "Head of Strategy", [], [], location="LISBON, PORTUGAL", location_filter=["portugal"],
        ))

    def test_location_filter_terms_are_ored(self):
        self.assertTrue(jobtrack_watch.matches(
            "Head of Strategy", [], [], location="Bucharest, Romania",
            location_filter=["Portugal", "Romania"],
        ))

    def test_empty_location_filter_matches_everything_unaffected(self):
        # Regression guard: every company without a Location override must
        # behave exactly as it did before this feature existed.
        self.assertTrue(jobtrack_watch.matches("Head of Strategy", [], [], location="Anywhere"))
        self.assertTrue(jobtrack_watch.matches("Head of Strategy", [], [], location=""))

    def test_title_include_and_location_filter_are_anded(self):
        # A title match with a non-matching location must still fail overall.
        self.assertFalse(jobtrack_watch.matches(
            "Country Director, Romania", ["director"], [],
            location="Bucharest, Romania", location_filter=["Portugal"],
        ))


class TestPoll(unittest.TestCase):
    TODAY = datetime.date(2026, 8, 4)

    def _run(self, watches, state, postings_by_source, defaults=None):
        wl = jobtrack_watch.Watchlist(
            defaults=defaults or jobtrack_watch.Filters(include=[], exclude=[]), watches=watches
        )

        def fake_fetch(source, timeout=0):
            value = postings_by_source[str(source)]
            if isinstance(value, Exception):
                raise value
            return value

        return jobtrack_watch.poll(wl, state, self.TODAY, fetch=fake_fetch)

    def test_first_run_seeds_and_notifies_nothing(self):
        state = {}
        results = self._run([_watch()], state, {"ashby:acme": [_posting("1", "Head of Strategy")]})
        self.assertTrue(results[0].seeded)
        self.assertEqual(results[0].new, [])
        self.assertEqual(state["ashby:acme"].seen_ids, ["1"])

    def test_second_run_reports_only_genuinely_new_ids(self):
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["1"], last_ok="2026-08-01")}
        results = self._run(
            [_watch()], state,
            {"ashby:acme": [_posting("1", "Head of Strategy"), _posting("2", "Director of Ops")]},
        )
        self.assertEqual([p.id for p in results[0].new], ["2"])
        self.assertFalse(results[0].seeded)

    def test_filtered_out_postings_are_not_reported(self):
        # seen_ids must be NON-empty and last_ok must be set, or the seeding
        # branch short-circuits and this test passes even with filtering
        # completely broken.
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["old"], last_ok="2026-08-01")}
        results = self._run(
            [_watch()], state, {"ashby:acme": [_posting("9", "Backend Engineer")]},
            defaults=jobtrack_watch.Filters(include=["strategy"], exclude=[]),
        )
        self.assertEqual(results[0].new, [])
        self.assertFalse(results[0].seeded)  # proves we took the filter path, not the seed path

    def test_a_matching_posting_on_the_same_run_is_reported(self):
        # paired with the test above: same state, same filters, matching title
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["old"], last_ok="2026-08-01")}
        results = self._run(
            [_watch()], state, {"ashby:acme": [_posting("9", "Head of Strategy")]},
            defaults=jobtrack_watch.Filters(include=["strategy"], exclude=[]),
        )
        self.assertEqual([p.id for p in results[0].new], ["9"])

    def test_filtered_out_postings_are_still_marked_seen(self):
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["old"], last_ok="2026-08-01")}
        self._run([_watch()], state, {"ashby:acme": [_posting("9", "Backend Engineer")]},
                  defaults=jobtrack_watch.Filters(include=["strategy"], exclude=[]))
        self.assertIn("9", state["ashby:acme"].seen_ids)

    def test_a_failing_source_does_not_abort_the_others(self):
        state = {
            "ashby:acme": jobtrack_watch.SourceState(seen_ids=[]),
            # last_ok set: this source has already been successfully polled
            # before, so its next poll must be evaluated for new postings,
            # not silently seeded.
            "ashby:good": jobtrack_watch.SourceState(seen_ids=["old"], last_ok="2026-08-01"),
        }
        results = self._run(
            [_watch(slug="acme"), _watch(company="Good", slug="good")],
            state,
            {"ashby:acme": jobtrack_watch.AdapterError("boom"),
             "ashby:good": [_posting("5", "Head of Strategy")]},
        )
        self.assertIsNotNone(results[0].error)
        self.assertIsNone(results[1].error)
        self.assertEqual([p.id for p in results[1].new], ["5"])

    def test_never_polled_source_seeds_and_notifies_nothing(self):
        # last_ok is None and seen_ids is empty: a genuinely never-polled
        # source, even though the state dict already has a key for it.
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=[], last_ok=None)}
        results = self._run([_watch()], state, {"ashby:acme": [_posting("1", "Head of Strategy")]})
        self.assertTrue(results[0].seeded)
        self.assertEqual(results[0].new, [])
        self.assertEqual(state["ashby:acme"].seen_ids, ["1"])

    def test_source_whose_first_poll_failed_still_seeds_on_first_success(self):
        # first poll failed: setdefault already created the entry, so
        # `key in state` is True, but last_ok is still None -- this must
        # seed silently on the next (successful) poll, not fire a storm.
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=[], last_ok=None, consecutive_failures=1)}
        results = self._run([_watch()], state, {"ashby:acme": [_posting("1", "A"), _posting("2", "B")]})
        self.assertTrue(results[0].seeded)
        self.assertEqual(results[0].new, [])
        self.assertEqual(state["ashby:acme"].seen_ids, ["1", "2"])

    def test_previously_empty_board_notifies_on_its_first_posting(self):
        # last_ok is set (a prior successful poll happened, board was
        # empty at the time) and seen_ids is empty. This is the case the
        # old "not entry.seen_ids" shortcut got wrong: a legitimately
        # empty board is not the same as "never polled," so its first
        # posting must notify, not seed silently.
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=[], last_ok="2026-08-01")}
        results = self._run([_watch()], state, {"ashby:acme": [_posting("1", "Head of Strategy")]})
        self.assertFalse(results[0].seeded)
        self.assertEqual([p.id for p in results[0].new], ["1"])
        self.assertEqual(state["ashby:acme"].seen_ids, ["1"])

    def test_normal_tracked_source_reports_only_genuinely_new_ids(self):
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["1"], last_ok="2026-08-01")}
        results = self._run(
            [_watch()], state,
            {"ashby:acme": [_posting("1", "Head of Strategy"), _posting("2", "Director of Ops")]},
        )
        self.assertFalse(results[0].seeded)
        self.assertEqual([p.id for p in results[0].new], ["2"])

    def test_failure_increments_the_counter_and_records_the_message(self):
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=[], consecutive_failures=1)}
        self._run([_watch()], state, {"ashby:acme": jobtrack_watch.AdapterError("boom")})
        self.assertEqual(state["ashby:acme"].consecutive_failures, 2)
        self.assertIn("boom", state["ashby:acme"].last_error)

    def test_success_resets_the_failure_counter_and_stamps_last_ok(self):
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["1"], consecutive_failures=3)}
        self._run([_watch()], state, {"ashby:acme": [_posting("1", "X")]})
        self.assertEqual(state["ashby:acme"].consecutive_failures, 0)
        self.assertEqual(state["ashby:acme"].last_ok, "2026-08-04")

    def test_a_failed_source_does_not_lose_its_seen_ids(self):
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["1", "2"])}
        self._run([_watch()], state, {"ashby:acme": jobtrack_watch.AdapterError("boom")})
        self.assertEqual(sorted(state["ashby:acme"].seen_ids), ["1", "2"])

    def test_location_filter_excludes_a_title_match_from_the_wrong_country(self):
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["old"], last_ok="2026-08-01")}
        watch = _watch(location=["Portugal"])
        results = self._run(
            [watch], state,
            {"ashby:acme": [_posting("9", "Country Director, Romania", location="Bucharest, Romania")]},
            defaults=jobtrack_watch.Filters(include=["director"], exclude=[]),
        )
        self.assertEqual(results[0].new, [])

    def test_location_filter_allows_a_title_match_from_the_right_country(self):
        state = {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["old"], last_ok="2026-08-01")}
        watch = _watch(location=["Portugal"])
        results = self._run(
            [watch], state,
            {"ashby:acme": [_posting("9", "Head of Strategy", location="Lisbon, Portugal")]},
            defaults=jobtrack_watch.Filters(include=["strategy"], exclude=[]),
        )
        self.assertEqual([p.id for p in results[0].new], ["9"])


class TestNotifier(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = os.path.join(self.tmp.name, ".env")
        self.access = os.path.join(self.tmp.name, "access.json")
        with open(self.env, "w", encoding="utf-8") as h:
            h.write("TELEGRAM_BOT_TOKEN=123456:FAKE-TOKEN-VALUE\n")
        with open(self.access, "w", encoding="utf-8") as h:
            json.dump({"dmPolicy": "allowlist", "allowFrom": [462000111]}, h)

    def test_reads_token_and_chat_id(self):
        token, chat = jobtrack_watch.read_telegram_credentials(self.env, self.access)
        self.assertEqual(token, "123456:FAKE-TOKEN-VALUE")
        self.assertEqual(chat, "462000111")

    def test_missing_env_raises_a_clear_error(self):
        with self.assertRaises(jobtrack_watch.NotifyError) as ctx:
            jobtrack_watch.read_telegram_credentials(
                os.path.join(self.tmp.name, "nope.env"), self.access
            )
        self.assertIn("token", str(ctx.exception).lower())

    def test_empty_allowlist_raises_a_clear_error(self):
        with open(self.access, "w", encoding="utf-8") as h:
            json.dump({"allowFrom": []}, h)
        with self.assertRaises(jobtrack_watch.NotifyError):
            jobtrack_watch.read_telegram_credentials(self.env, self.access)

    def test_notification_text_names_company_title_location_and_link(self):
        result = jobtrack_watch.PollResult(
            company="Acme", source=jobtrack_watch.Source("ashby", "acme"),
            new=[_posting("1", "Head of Strategy", "Porto")],
        )
        text = jobtrack_watch.format_notification(result)
        self.assertIn("Acme", text)
        self.assertIn("Head of Strategy", text)
        self.assertIn("Porto", text)
        self.assertIn("https://e.x/1", text)

    def test_notification_lists_every_new_posting(self):
        result = jobtrack_watch.PollResult(
            company="Acme", source=jobtrack_watch.Source("ashby", "acme"),
            new=[_posting("1", "Head of Strategy"), _posting("2", "Director of Ops")],
        )
        text = jobtrack_watch.format_notification(result)
        self.assertIn("Head of Strategy", text)
        self.assertIn("Director of Ops", text)

    def test_send_posts_to_the_bot_api_with_the_chat_id(self):
        calls = []

        def opener(request, timeout=0):
            calls.append((request.full_url, request.data))
            class R:
                def read(self): return b'{"ok":true}'
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R()

        jobtrack_watch.send_telegram("hello", "TOK", "123", opener=opener)
        self.assertEqual(len(calls), 1)
        url, data = calls[0]
        self.assertIn("api.telegram.org/botTOK/sendMessage", url)
        self.assertIn(b"123", data)

    def test_send_failure_does_not_leak_the_token_in_the_error(self):
        def opener(request, timeout=0):
            raise OSError("failed opening https://api.telegram.org/botSECRETTOKEN/sendMessage")

        with self.assertRaises(jobtrack_watch.NotifyError) as ctx:
            jobtrack_watch.send_telegram("hi", "SECRETTOKEN", "123", opener=opener)
        self.assertNotIn("SECRETTOKEN", str(ctx.exception))

    def test_notify_skips_results_with_no_new_postings(self):
        sent = []
        def opener(request, timeout=0):
            sent.append(request.full_url)
            class R:
                def read(self): return b'{"ok":true}'
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R()

        results = [
            jobtrack_watch.PollResult("A", jobtrack_watch.Source("ashby", "a"), new=[]),
            jobtrack_watch.PollResult("B", jobtrack_watch.Source("ashby", "b"),
                                      new=[_posting("1", "Head of Strategy")]),
        ]
        report = jobtrack_watch.notify(
            results, opener=opener, credentials=("TOK", "123")
        )
        self.assertEqual(report.sent, 1)
        self.assertEqual(report.failed, [])
        self.assertEqual(len(sent), 1)

    def test_split_notification_breaks_between_postings_not_mid_posting(self):
        # Each block is well over 100 chars; limit=250 forces multiple chunks.
        postings = [
            _posting(str(i), "Head of Strategy and Long Titled Role Number {}".format(i),
                     "Some City, Some Region, Some Country")
            for i in range(10)
        ]
        result = jobtrack_watch.PollResult(
            company="Acme", source=jobtrack_watch.Source("ashby", "acme"), new=postings
        )
        messages = jobtrack_watch.split_notification(result, limit=250)
        self.assertGreater(len(messages), 1)
        for message in messages:
            self.assertLessEqual(len(message), 250)
        # Every posting's title and url must land together, intact, in exactly one message.
        for posting in postings:
            containing = [m for m in messages if posting.title in m and posting.url in m]
            self.assertEqual(
                len(containing), 1,
                "posting {} was split or duplicated across messages".format(posting.id),
            )

    def test_split_notification_keeps_a_message_under_the_limit_as_one(self):
        result = jobtrack_watch.PollResult(
            company="Acme", source=jobtrack_watch.Source("ashby", "acme"),
            new=[_posting("1", "Head of Strategy", "Porto")],
        )
        text = jobtrack_watch.format_notification(result)
        messages = jobtrack_watch.split_notification(result, limit=len(text) + 10)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0], text)

    def test_notify_isolates_a_failing_company_from_the_rest(self):
        sent_urls = []

        def opener(request, timeout=0):
            if b"A-fails" in request.data:
                raise OSError("simulated failure")
            sent_urls.append(request.full_url)
            class R:
                def read(self): return b'{"ok":true}'
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R()

        results = [
            jobtrack_watch.PollResult(
                "A", jobtrack_watch.Source("ashby", "a"),
                new=[_posting("1", "A-fails role")],
            ),
            jobtrack_watch.PollResult(
                "B", jobtrack_watch.Source("ashby", "b"),
                new=[_posting("2", "Head of Strategy")],
            ),
        ]
        report = jobtrack_watch.notify(results, opener=opener, credentials=("TOK", "123"))
        self.assertEqual(report.sent, 1)
        self.assertEqual(report.failed, ["A"])
        self.assertEqual(len(sent_urls), 1)

    def test_missing_env_with_bad_encoding_raises_notify_error(self):
        with open(self.env, "wb") as h:
            h.write(b"TELEGRAM_BOT_TOKEN=FAKE\xff\xfeBROKEN\n")
        with self.assertRaises(jobtrack_watch.NotifyError) as ctx:
            jobtrack_watch.read_telegram_credentials(self.env, self.access)
        message = str(ctx.exception)
        self.assertNotIn("\xff", message)
        self.assertNotIn("\xfe", message)
        self.assertNotIn("BROKEN", message)

    def test_malformed_access_json_raises_notify_error(self):
        with open(self.access, "w", encoding="utf-8") as h:
            h.write("{not valid json,,,")
        with self.assertRaises(jobtrack_watch.NotifyError):
            jobtrack_watch.read_telegram_credentials(self.env, self.access)

    def test_non_utf8_access_json_raises_notify_error(self):
        with open(self.access, "wb") as h:
            h.write(b'{"allowFrom": [\xff\xfe123]}')
        with self.assertRaises(jobtrack_watch.NotifyError) as ctx:
            jobtrack_watch.read_telegram_credentials(self.env, self.access)
        message = str(ctx.exception)
        self.assertNotIn("\xff", message)
        self.assertNotIn("\xfe", message)


class TestWatchWriters(unittest.TestCase):
    def test_add_appends_a_parseable_row(self):
        updated = jobtrack_watch.add_watch(
            GOOD, "Acme", jobtrack_watch.Source("ashby", "acme"), None, datetime.date(2026, 8, 5)
        )
        wl = jobtrack_watch.parse_watchlist(updated)
        self.assertEqual([w.company for w in wl.watches], ["Umbrella", "Remote", "Acme"])
        self.assertEqual(wl.watches[-1].keywords, None)

    def test_add_writes_keyword_override(self):
        updated = jobtrack_watch.add_watch(
            GOOD, "Acme", jobtrack_watch.Source("ashby", "acme"), ["strategy", "product"],
            datetime.date(2026, 8, 5),
        )
        self.assertEqual(jobtrack_watch.parse_watchlist(updated).watches[-1].keywords,
                         ["strategy", "product"])

    def test_add_rejects_duplicate_company(self):
        with self.assertRaises(jobtrack.Duplicate):
            jobtrack_watch.add_watch(
                GOOD, "Umbrella", jobtrack_watch.Source("ashby", "x"), None,
                datetime.date(2026, 8, 5),
            )

    def test_add_rejects_pipe_in_company(self):
        with self.assertRaises(ValueError):
            jobtrack_watch.add_watch(
                GOOD, "Ac|me", jobtrack_watch.Source("ashby", "x"), None, datetime.date(2026, 8, 5)
            )

    def test_add_leaves_other_rows_byte_identical(self):
        updated = jobtrack_watch.add_watch(
            GOOD, "Acme", jobtrack_watch.Source("ashby", "acme"), None, datetime.date(2026, 8, 5)
        )
        self.assertEqual(
            [l for l in GOOD.splitlines() if "Acme" not in l],
            [l for l in updated.splitlines() if "Acme" not in l],
        )

    def test_remove_deletes_only_that_row(self):
        updated = jobtrack_watch.remove_watch(GOOD, "Umbrella")
        wl = jobtrack_watch.parse_watchlist(updated)
        self.assertEqual([w.company for w in wl.watches], ["Remote"])

    def test_remove_unknown_company_raises_not_found(self):
        with self.assertRaises(jobtrack.NotFound):
            jobtrack_watch.remove_watch(GOOD, "Nobody")

    def test_remove_preserves_the_default_filter_block(self):
        wl = jobtrack_watch.parse_watchlist(jobtrack_watch.remove_watch(GOOD, "Remote"))
        self.assertEqual(wl.defaults.include, ["strategy", "product manager", "transformation"])

    def test_add_rejects_the_header_literal_as_company(self):
        # Without this guard, add_watch(text, "Company", ...) writes a row
        # that every reader (parse_watchlist, format_status, poll) treats as
        # the table header and silently skips -- an invisible, unremovable
        # orphan row (remove_watch can't find it either).
        with self.assertRaises(ValueError):
            jobtrack_watch.add_watch(
                GOOD, "Company", jobtrack_watch.Source("ashby", "x"), None, datetime.date(2026, 8, 5)
            )

    def test_add_rejects_comma_in_keyword(self):
        # A keyword containing a comma would silently mis-split into two
        # keywords the next time the row is parsed.
        with self.assertRaises(ValueError):
            jobtrack_watch.add_watch(
                GOOD, "Acme", jobtrack_watch.Source("ashby", "x"), ["a,b"], datetime.date(2026, 8, 5)
            )

    def test_add_writes_location_override(self):
        updated = jobtrack_watch.add_watch(
            GOOD, "Acme", jobtrack_watch.Source("ashby", "acme"), None,
            datetime.date(2026, 8, 5), location=["Portugal"],
        )
        self.assertEqual(jobtrack_watch.parse_watchlist(updated).watches[-1].location, ["Portugal"])

    def test_add_without_location_writes_the_placeholder(self):
        updated = jobtrack_watch.add_watch(
            GOOD, "Acme", jobtrack_watch.Source("ashby", "acme"), None, datetime.date(2026, 8, 5)
        )
        self.assertIn("| — | 2026-08-05 |", updated)
        self.assertIsNone(jobtrack_watch.parse_watchlist(updated).watches[-1].location)


class TestDryRunPersistence(unittest.TestCase):
    """`--dry-run` must have zero persistent side effects. poll() marks every
    fetched id as seen in the in-memory state dict regardless of
    notify_enabled, so run_poll must skip save_state on a dry run -- otherwise
    a dry run tells you a role is new, marks it seen on disk, and the next
    REAL poll then stays silent about it forever."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wl_path = os.path.join(self.tmp.name, "wl.md")
        self.state_path = os.path.join(self.tmp.name, "state.json")
        with open(self.wl_path, "w", encoding="utf-8") as h:
            h.write(
                "# Watchlist\n\n"
                "- include: strategy\n- exclude: intern\n\n"
                "| Company | Source | Keywords | Location | Added |\n|---|---|---|---|---|\n"
                "| Acme | ashby:acme | — | — | 2026-08-04 |\n"
            )
        jobtrack_watch.save_state(
            self.state_path,
            {"ashby:acme": jobtrack_watch.SourceState(seen_ids=["old"], last_ok="2026-08-01")},
        )

    def _fetch(self, source, timeout=0):
        return [
            jobtrack_watch.Posting(id="old", title="Head of Strategy", url="https://e.x/old",
                                    location="Porto", posted="2026-08-01"),
            jobtrack_watch.Posting(id="NEW", title="Head of Strategy II", url="https://e.x/new",
                                    location="Porto", posted="2026-08-04"),
        ]

    def test_dry_run_leaves_the_state_file_byte_identical(self):
        with open(self.state_path, "rb") as h:
            before = h.read()
        results = jobtrack_watch.run_poll(
            self.wl_path, self.state_path, datetime.date(2026, 8, 4),
            fetch=self._fetch, notify_enabled=False,
        )
        self.assertEqual([p.id for p in results[0].new], ["NEW"])
        with open(self.state_path, "rb") as h:
            after = h.read()
        self.assertEqual(before, after)

    def test_dry_run_does_not_suppress_the_next_real_polls_report(self):
        jobtrack_watch.run_poll(
            self.wl_path, self.state_path, datetime.date(2026, 8, 4),
            fetch=self._fetch, notify_enabled=False,
        )

        def fake_opener(request, timeout=0):
            class R:
                def read(self):
                    return b'{"ok":true}'

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()

        with mock.patch.object(
            jobtrack_watch, "read_telegram_credentials", return_value=("TOK", "123")
        ):
            results = jobtrack_watch.run_poll(
                self.wl_path, self.state_path, datetime.date(2026, 8, 4),
                fetch=self._fetch, opener=fake_opener, notify_enabled=True,
            )
        self.assertEqual([p.id for p in results[0].new], ["NEW"])


class TestNullIdIsNotAPhantomPosting(unittest.TestCase):
    """An explicit `"id": null` must be treated as "no id", not as the string
    "None". str(job.get("id", "")) returns "None" for a PRESENT-but-null key --
    dict.get only applies its default when the key is ABSENT -- and "None" is
    truthy, so it slipped past every parser's no-id skip guard. Two null-id
    records then collapse onto one seen id and the second is lost."""

    def test_greenhouse_skips_null_and_false_ids(self):
        payload = {"jobs": [
            {"id": None, "title": "Phantom"},
            {"id": False, "title": "Also phantom"},
            {"id": 7, "title": "Real", "absolute_url": "http://e.x/7"},
        ]}
        postings = jobtrack_watch.parse_greenhouse(payload)
        self.assertEqual([p.id for p in postings], ["7"])

    def test_lever_skips_null_and_false_ids(self):
        payload = [
            {"id": None, "text": "Phantom"},
            {"id": False, "text": "Also phantom"},
            {"id": "a", "text": "Real", "hostedUrl": "http://e.x/a"},
        ]
        postings = jobtrack_watch.parse_lever(payload)
        self.assertEqual([p.id for p in postings], ["a"])

    def test_ashby_skips_null_and_false_ids(self):
        payload = {"jobs": [
            {"id": None, "title": "Phantom"},
            {"id": False, "title": "Also phantom"},
            {"id": "x", "title": "Real", "jobUrl": "http://e.x/x"},
        ]}
        postings = jobtrack_watch.parse_ashby(payload)
        self.assertEqual([p.id for p in postings], ["x"])

    def test_smartrecruiters_skips_null_and_false_ids(self):
        payload = {"content": [
            {"id": None, "name": "Phantom"},
            {"id": False, "name": "Also phantom"},
            {"id": 3, "name": "Real"},
        ]}
        postings = jobtrack_watch.parse_smartrecruiters(payload, "acme")
        self.assertEqual([p.id for p in postings], ["3"])

    def test_two_null_ids_do_not_collapse_into_one_seen_id(self):
        # The regression this actually causes: both records used to arrive as
        # id "None", so the diff treated the second as already-seen.
        payload = {"jobs": [{"id": None, "title": "A"}, {"id": None, "title": "B"}]}
        self.assertEqual(jobtrack_watch.parse_greenhouse(payload), [])


class TestNotifyBeforeSave(unittest.TestCase):
    """A role the user was never told about must not be marked seen.

    run_poll used to save_state BEFORE notify(). poll() records every fetched
    id as seen, so a failed Telegram send committed those ids to disk anyway:
    the role could never be "new" again and was silently lost forever, while
    the CLI still exited 0 for the LaunchAgent. Notify first, and back the
    unnotified matched ids out of state before saving.
    """

    WATCHLIST = (
        "# Watchlist\n\n- include: director\n- exclude: intern\n\n"
        "| Company | Source | Keywords | Location | Added |\n|---|---|---|---|---|\n"
        "| Acme | ashby:acme | — | — | 2026-08-04 |\n"
        "| Globex | ashby:globex | — | — | 2026-08-04 |\n"
    )
    TODAY = datetime.date(2026, 8, 4)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wl_path = os.path.join(self.tmp.name, "wl.md")
        self.state_path = os.path.join(self.tmp.name, "state.json")
        with open(self.wl_path, "w", encoding="utf-8") as h:
            h.write(self.WATCHLIST)
        jobtrack_watch.save_state(self.state_path, {
            "ashby:acme": jobtrack_watch.SourceState(seen_ids=["1"], last_ok="2026-08-01"),
            "ashby:globex": jobtrack_watch.SourceState(seen_ids=["g1"], last_ok="2026-08-01"),
        })

    def _fetch(self, source, timeout=0):
        if source.slug == "acme":
            return [
                jobtrack_watch.Posting("1", "Director of Ops", "http://e.x/1", "Porto", None),
                jobtrack_watch.Posting("2", "Director of Product", "http://e.x/2", "Porto", None),
                jobtrack_watch.Posting("3", "Barista", "http://e.x/3", "Porto", None),
            ]
        return [
            jobtrack_watch.Posting("g1", "Director of Sales", "http://e.x/g1", "Lisboa", None),
            jobtrack_watch.Posting("g2", "Director of Data", "http://e.x/g2", "Lisboa", None),
        ]

    @staticmethod
    def _opener(fail_for=()):
        """A urlopen stand-in that raises for any message naming a failing company."""

        class _Response:
            def read(self):
                return b'{"ok":true}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=0):
            body = request.data.decode("utf-8")
            for needle in fail_for:
                if urllib.parse.quote(needle) in body or needle in body:
                    raise urllib.error.HTTPError(
                        "https://api.telegram.org/", 400, "Bad Request", {}, None
                    )
            return _Response()

        return opener

    def _run(self, opener, credentials=("TOK", "123")):
        with mock.patch.object(
            jobtrack_watch, "read_telegram_credentials", return_value=credentials
        ):
            return jobtrack_watch.run_poll(
                self.wl_path, self.state_path, self.TODAY,
                fetch=self._fetch, opener=opener,
            )

    def _seen(self, key):
        return jobtrack_watch.load_state(self.state_path)[key].seen_ids

    def test_failed_send_leaves_its_new_matched_ids_unseen(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            results = self._run(self._opener(fail_for=("Acme",)))
        self.assertEqual([p.id for p in results[0].new], ["2"])
        self.assertNotIn("2", self._seen("ashby:acme"))

    def test_the_next_poll_re_reports_a_role_whose_send_failed(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            self._run(self._opener(fail_for=("Acme",)))
            again = self._run(self._opener())  # Telegram healthy again
        self.assertEqual([p.id for p in again[0].new], ["2"])
        self.assertIn("2", self._seen("ashby:acme"))

    def test_failed_send_does_not_un_see_non_matching_postings(self):
        # "Barista" (id 3) never matched the filters, so it was never going to
        # be notified. Un-seeing it would make every future poll re-evaluate
        # the whole board forever.
        with mock.patch("sys.stderr", new=io.StringIO()):
            self._run(self._opener(fail_for=("Acme",)))
        seen = self._seen("ashby:acme")
        self.assertIn("3", seen)
        self.assertIn("1", seen)

    def test_one_companys_failure_does_not_roll_back_anothers_success(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            self._run(self._opener(fail_for=("Acme",)))
        self.assertNotIn("2", self._seen("ashby:acme"))
        self.assertIn("g2", self._seen("ashby:globex"))

    def test_successful_send_saves_normally(self):
        results = self._run(self._opener())
        self.assertEqual([p.id for p in results[0].new], ["2"])
        self.assertEqual(self._seen("ashby:acme"), ["1", "2", "3"])
        self.assertEqual(self._seen("ashby:globex"), ["g1", "g2"])

    def test_a_credentials_failure_saves_nothing(self):
        with open(self.state_path, "rb") as h:
            before = h.read()
        with mock.patch.object(
            jobtrack_watch,
            "read_telegram_credentials",
            side_effect=jobtrack_watch.NotifyError("could not read the Telegram bot token"),
        ):
            with self.assertRaises(jobtrack_watch.NotifyError):
                jobtrack_watch.run_poll(
                    self.wl_path, self.state_path, self.TODAY,
                    fetch=self._fetch, opener=self._opener(),
                )
        with open(self.state_path, "rb") as h:
            self.assertEqual(before, h.read())

    def test_a_failed_send_warns_on_stderr_naming_the_company(self):
        stderr = io.StringIO()
        with mock.patch("sys.stderr", new=stderr):
            self._run(self._opener(fail_for=("Acme",)))
        self.assertIn("Acme", stderr.getvalue())
        self.assertIn("failed", stderr.getvalue())
        self.assertNotIn("TOK", stderr.getvalue())


class TestWatchAddStateSeedingFailure(unittest.TestCase):
    """`watch add` must not report a successful add as a failure.

    write_log_atomic commits the watchlist row; a save_state failure AFTER
    that used to fall through to main()'s generic OSError handler, printing a
    raw errno and exiting 1 for an operation that had already succeeded. The
    user then re-runs it and gets a spurious "already on the watchlist".
    Mirrors the fix already made in jobtrack_web._add_watch.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wl_path = os.path.join(self.tmp.name, "wl.md")
        with open(self.wl_path, "w", encoding="utf-8") as h:
            h.write(
                "# Watchlist\n\n- include: strategy\n- exclude: intern\n\n"
                "| Company | Source | Keywords | Location | Added |\n|---|---|---|---|---|\n"
                "| Existing | ashby:existing | — | — | 2026-08-01 |\n"
            )
        # A state path inside a directory that does not exist: write_log_atomic's
        # mkstemp(dir=...) raises OSError, with no permission games needed.
        self.state_path = os.path.join(self.tmp.name, "nope", "state.json")

    def _add(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("sys.stderr", new=err):
            with mock.patch.object(
                jobtrack_watch, "fetch_postings",
                return_value=[jobtrack_watch.Posting("1", "T", "http://e.x/1", "", None)],
            ):
                code = jobtrack.main([
                    "watch", "--watchlist", self.wl_path, "--state", self.state_path,
                    "add", "--url", "https://boards.greenhouse.io/acme", "--company", "Acme",
                ])
        return code, out.getvalue(), err.getvalue()

    def test_a_state_seeding_failure_is_not_reported_as_a_failed_add(self):
        code, out, err = self._add()
        self.assertEqual(code, 0)
        self.assertIn("Watching Acme", out)
        with open(self.wl_path, encoding="utf-8") as h:
            self.assertIn("| Acme | greenhouse:acme |", h.read())

    def test_the_warning_names_the_state_file_and_says_the_next_poll_seeds(self):
        _, _, err = self._add()
        self.assertIn(self.state_path, err)
        self.assertNotIn(self.wl_path, err)  # the watchlist wrote fine; don't blame it
        self.assertIn("next poll", err)

    def test_a_successful_add_still_reports_the_seeded_posting_count(self):
        self.state_path = os.path.join(self.tmp.name, "state.json")
        code, out, err = self._add()
        self.assertEqual(code, 0)
        self.assertIn("1 current posting(s) recorded as seen", out)
        self.assertEqual(err, "")
        state = jobtrack_watch.load_state(self.state_path)
        self.assertEqual(state["greenhouse:acme"].seen_ids, ["1"])


class TestWatchCLISubprocess(unittest.TestCase):
    """`python3 tools/jobtrack.py watch ...` invoked as a real subprocess.

    This is the only way to catch the dual-module-identity bug: when
    jobtrack.py runs as __main__, jobtrack_watch's own `import jobtrack`
    loads tools/jobtrack.py a SECOND time under the module name "jobtrack" --
    a distinct module object with its own distinct ParseError/NotFound/
    Duplicate classes. main()'s `except (ParseError, NotFound, Duplicate,
    ...)` binds to __main__'s classes, so an exception raised by
    jobtrack_watch (the OTHER instance's class) would not match and would
    escape as a raw traceback. In-process calls to jobtrack.main(...) share
    this test file's own `import jobtrack` module object with jobtrack_watch
    and structurally cannot reproduce the split.
    """

    JOBTRACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobtrack.py")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wl_path = os.path.join(self.tmp.name, "wl.md")
        self.state_path = os.path.join(self.tmp.name, "state.json")

    def _write_watchlist(self, text):
        with open(self.wl_path, "w", encoding="utf-8") as h:
            h.write(text)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, self.JOBTRACK, "watch",
             "--watchlist", self.wl_path, "--state", self.state_path] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_remove_unknown_company_is_a_clean_error_not_a_traceback(self):
        self._write_watchlist(GOOD)
        result = self._run("remove", "--company", "Nobody")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("is not on the watchlist", result.stderr)

    def test_duplicate_add_is_a_clean_error_not_a_traceback(self):
        # Company already present, so add_watch's duplicate check fires
        # before the CLI ever reaches the network fetch -- this test stays
        # fully offline even though the URL below names a real board.
        self._write_watchlist(
            "# Watchlist\n\n- include: strategy\n- exclude: intern\n\n"
            "| Company | Source | Keywords | Location | Added |\n|---|---|---|---|---|\n"
            "| Acme | ashby:acme | — | — | 2026-08-04 |\n"
        )
        result = self._run("add", "--url", "https://jobs.ashbyhq.com/acme", "--company", "Acme")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("already on the watchlist", result.stderr)

    def test_malformed_watchlist_on_status_is_a_clean_error_not_a_traceback(self):
        self._write_watchlist(GOOD.replace("ashby:umbrella", "bogusvendor:umbrella"))
        result = self._run("status")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        # The error must name the file actually being parsed (--watchlist
        # wl.md), not the default watchlist.md the user never touched.
        self.assertIn("wl.md line", result.stderr)
        self.assertNotIn("watchlist.md", result.stderr)
        self.assertIn("unknown vendor", result.stderr)

    def test_malformed_watchlist_on_poll_names_the_file_given(self):
        self._write_watchlist(GOOD.replace("ashby:umbrella", "bogusvendor:umbrella"))
        result = self._run("poll", "--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("wl.md line", result.stderr)
        self.assertNotIn("watchlist.md", result.stderr)

    def test_poll_on_a_missing_watchlist_is_the_friendly_not_initialised_error(self):
        # Every other subcommand routes through jobtrack._load; poll used to
        # call read_log directly and leak a raw "[Errno 2] No such file or
        # directory" instead of the same friendly message.
        result = self._run("poll", "--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Errno 2", result.stderr)
        self.assertIn("not initialised", result.stderr)
        self.assertIn(self.wl_path, result.stderr)


if __name__ == "__main__":
    unittest.main()


class TestFollowPosting(unittest.TestCase):
    """Adding an application with a posting link follows that company."""

    TODAY = datetime.date(2026, 10, 14)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wl = os.path.join(self.tmp.name, "watchlist.md")
        self.state = os.path.join(self.tmp.name, "state.json")
        self.follows = os.path.join(self.tmp.name, "follow.json")
        with open(self.wl, "w", encoding="utf-8") as h:
            h.write(GOOD)
        fetch = mock.patch.object(
            jobtrack_watch, "fetch_postings",
            return_value=[jobtrack_watch.Posting("1", "T", "http://e.x/1", "", None)],
        )
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)

    def _watchlist(self):
        return jobtrack_watch.parse_watchlist(jobtrack.read_log(self.wl))

    def test_a_supported_posting_adds_the_company_and_seeds_its_postings(self):
        result = jobtrack_watch.follow_posting(
            "Toggl", "https://jobs.ashbyhq.com/toggl/abc-123", self.wl, self.state, self.TODAY
        )
        self.assertEqual(result.outcome, jobtrack_watch.FOLLOWED)
        self.assertIn("Toggl", [w.company for w in self._watchlist().watches])
        self.assertEqual(jobtrack_watch.load_state(self.state)["ashby:toggl"].seen_ids, ["1"])

    def test_a_board_already_watched_under_another_name_is_not_added_twice(self):
        result = jobtrack_watch.follow_posting(
            "Umbrella Lda", "https://jobs.ashbyhq.com/umbrella/x", self.wl, self.state,
            self.TODAY,
        )
        self.assertEqual(result.outcome, jobtrack_watch.ALREADY)
        self.assertEqual(result.company, "Umbrella")
        self.assertEqual(len(self._watchlist().watches), 2)
        self.fetch.assert_not_called()

    def test_an_unsupported_board_is_reported_and_leaves_the_watchlist_alone(self):
        before = jobtrack.read_log(self.wl)
        result = jobtrack_watch.follow_posting(
            "OutSystems", "https://careers-outsystems.icims.com/jobs/1/job", self.wl,
            self.state, self.TODAY,
        )
        self.assertEqual(result.outcome, jobtrack_watch.UNSUPPORTED)
        self.assertIn("iCIMS", result.detail)
        self.assertEqual(jobtrack.read_log(self.wl), before)

    def test_a_board_that_fails_to_fetch_is_still_followed_and_seeds_on_next_poll(self):
        self.fetch.side_effect = jobtrack_watch.AdapterError("down")
        result = jobtrack_watch.follow_posting(
            "Toggl", "https://jobs.ashbyhq.com/toggl/abc", self.wl, self.state, self.TODAY
        )
        self.assertEqual(result.outcome, jobtrack_watch.FOLLOWED)
        self.assertNotIn("ashby:toggl", jobtrack_watch.load_state(self.state))

    def _app(self, company, posting):
        return jobtrack.Application(company, "PM", self.TODAY, "cv", "applied", self.TODAY, posting)

    def test_follow_postings_tries_each_link_once_and_retries_unsupported_on_request(self):
        apps = [
            self._app("Toggl", "https://jobs.ashbyhq.com/toggl/abc"),
            self._app("OutSystems", "https://careers-outsystems.icims.com/jobs/1/job"),
            self._app("Hooli", "—"),
        ]
        first = jobtrack_watch.follow_postings(apps, self.wl, self.state, self.follows, self.TODAY)
        self.assertEqual([r.outcome for r in first], ["followed", "unsupported"])
        again = jobtrack_watch.follow_postings(apps, self.wl, self.state, self.follows, self.TODAY)
        self.assertEqual(again, [])
        retried = jobtrack_watch.follow_postings(
            apps, self.wl, self.state, self.follows, self.TODAY, retry=True
        )
        self.assertEqual([r.company for r in retried], ["OutSystems"])

    def test_following_records_list_watched_then_unwatchable_companies(self):
        apps = [self._app("OutSystems", "https://careers-outsystems.icims.com/jobs/1/job")]
        jobtrack_watch.follow_postings(apps, self.wl, self.state, self.follows, self.TODAY)
        state = {"ashby:umbrella": jobtrack_watch.SourceState(["a", "b"], "2026-10-13")}
        rows = jobtrack_watch.following_records(
            self._watchlist(), state, jobtrack_watch.load_follows(self.follows)
        )
        by_company = {r["company"]: r for r in rows}
        self.assertEqual(by_company["Umbrella"]["status"], "watching")
        self.assertEqual(by_company["Umbrella"]["tracked"], 2)
        self.assertEqual(by_company["Remote"]["status"], "pending")
        self.assertEqual(by_company["OutSystems"]["status"], "unsupported")
        self.assertEqual(by_company["OutSystems"]["board"], "iCIMS")
        self.assertEqual(by_company["OutSystems"]["note"], "Uses iCIMS, which the watcher cannot read yet.")


class TestAddFollowsPosting(unittest.TestCase):
    """`jobtrack add --posting URL` follows the company; it never fails the add."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "applications.md")
        self.wl = os.path.join(self.tmp.name, "watchlist.md")
        jobtrack.write_log_atomic(
            self.log,
            "# Applications\n\n| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | PM | 2026-07-24 | cv_northwind | applied | 2026-07-24 | — |\n",
        )
        with open(self.wl, "w", encoding="utf-8") as h:
            h.write(GOOD)
        fetch = mock.patch.object(jobtrack_watch, "fetch_postings", return_value=[])
        fetch.start()
        self.addCleanup(fetch.stop)

    def _add(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = jobtrack.main(
                ["add", "--company", "Toggl", "--role", "Senior PM", "--file", self.log] + list(extra)
            )
        return code, out.getvalue(), err.getvalue()

    def test_add_with_a_supported_posting_follows_the_company(self):
        code, out, _ = self._add("--posting", "https://jobs.ashbyhq.com/toggl/abc")
        self.assertEqual(code, 0)
        self.assertIn("Following Toggl via ashby:toggl", out)
        self.assertIn("| Toggl | ashby:toggl |", jobtrack.read_log(self.wl))
        follows = jobtrack_watch.load_follows(os.path.join(self.tmp.name, ".jobkit-follow.json"))
        self.assertEqual(follows["https://jobs.ashbyhq.com/toggl/abc"]["outcome"], "followed")

    def test_no_follow_leaves_the_watchlist_alone(self):
        self._add("--posting", "https://jobs.ashbyhq.com/toggl/abc", "--no-follow")
        self.assertNotIn("Toggl", jobtrack.read_log(self.wl))

    def test_an_unsupported_board_still_adds_the_application(self):
        code, out, _ = self._add("--posting", "https://careers-acme.icims.com/jobs/1/job")
        self.assertEqual(code, 0)
        self.assertIn("Added Toggl", out)
        self.assertIn("Not following Toggl", out)
        self.assertIn("No connector for this board yet; build one", out)


class TestGreenhouseEmbed(unittest.TestCase):
    def test_an_embedded_board_resolves_to_the_company_not_to_embed(self):
        html = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=feedzai"></script>'
        source = jobtrack_watch._resolve_from_html(html, "https://careers.feedzai.com/")
        self.assertEqual(str(source), "greenhouse:feedzai")


class TestAshbyBoards(unittest.TestCase):
    def test_a_slug_with_a_domain_keeps_the_domain(self):
        source = jobtrack_watch.resolve_url(
            "https://jobs.ashbyhq.com/happyrobot.ai/fe93?src=Linkedin", fetch=False
        )
        self.assertEqual(str(source), "ashby:happyrobot.ai")

    def test_a_404_from_the_public_api_falls_back_to_the_board_query(self):
        board = {"data": {"jobBoard": {"jobPostings": [
            {"id": "a1", "title": "Senior PM", "locationName": "Remote"},
        ]}}}

        class R:
            def __init__(self, body):
                self.body = body

            def read(self):
                return json.dumps(self.body).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            if "posting-api" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            return R(board)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            postings = jobtrack_watch.fetch_postings(jobtrack_watch.Source("ashby", "toggl"))
        self.assertEqual([(p.id, p.title, p.location) for p in postings], [("a1", "Senior PM", "Remote")])
        self.assertEqual(postings[0].url, "https://jobs.ashbyhq.com/toggl/a1")

    def test_a_missing_board_is_an_adapter_error(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_ashby_board({"data": {"jobBoard": None}}, "nobody")


class TestUnknownLocation(unittest.TestCase):
    def test_a_posting_without_a_location_is_not_hidden_by_the_location_filter(self):
        self.assertTrue(jobtrack_watch.matches("Senior Strategy Consultant", ["strategy"], [], "", ["Portugal"]))
        self.assertFalse(jobtrack_watch.matches("Senior Strategy Consultant", ["strategy"], [], "Houston, US", ["Portugal"]))


class TestWorkdayAdapter(unittest.TestCase):
    SLUG = "outsystems.wd503.myworkdayjobs.com/outsystems/OutSystems"

    def test_parses_postings_and_builds_board_links(self):
        postings = jobtrack_watch.parse_workday(_fixture("workday.json"), self.SLUG)
        self.assertEqual([p.title for p in postings], ["Digital Transformation Lead", "Strategy Manager"])
        self.assertEqual(
            postings[0].url,
            "https://outsystems.wd503.myworkdayjobs.com/OutSystems/job/Portugal---Lisbon/"
            "Digital-Transformation-Lead_R1628",
        )
        self.assertEqual(postings[0].location, "Portugal - Lisbon")

    def test_a_multi_location_count_is_treated_as_unknown_location(self):
        postings = jobtrack_watch.parse_workday(_fixture("workday.json"), self.SLUG)
        self.assertEqual(postings[1].location, "")

    def test_pages_by_offset_until_the_first_pages_total(self):
        page = {"total": 25, "jobPostings": [
            {"title": "T{}".format(i), "externalPath": "/job/x/T{}".format(i)} for i in range(20)
        ]}
        last = {"total": 0, "jobPostings": [
            {"title": "T{}".format(i), "externalPath": "/job/x/T{}".format(i)} for i in range(20, 25)
        ]}
        calls = []

        def fake_post(url, body, timeout):
            calls.append(body["offset"])
            return page if body["offset"] == 0 else last

        with mock.patch.object(jobtrack_watch, "_post_json", side_effect=fake_post):
            postings = jobtrack_watch.fetch_postings(jobtrack_watch.Source("workday", self.SLUG))
        self.assertEqual(calls, [0, 20])
        self.assertEqual(len(postings), 25)

    def test_resolves_job_links_with_and_without_a_locale(self):
        for url in (
            "https://outsystems.wd503.myworkdayjobs.com/OutSystems/job/Portugal---Lisbon/X_R1628",
            "https://outsystems.wd503.myworkdayjobs.com/en-US/OutSystems",
        ):
            self.assertEqual(str(jobtrack_watch.resolve_url(url, fetch=False)), "workday:" + self.SLUG)

    def test_resolves_the_shared_myworkdaysite_host(self):
        source = jobtrack_watch.resolve_url(
            "https://wd3.myworkdaysite.com/en-US/recruiting/acme/External/job/X_1", fetch=False
        )
        self.assertEqual(str(source), "workday:wd3.myworkdaysite.com/acme/External")
        posting = jobtrack_watch.parse_workday(
            {"jobPostings": [{"title": "T", "externalPath": "/job/X_1"}]}, source.slug
        )[0]
        self.assertEqual(posting.url, "https://wd3.myworkdaysite.com/recruiting/acme/External/job/X_1")

    def test_a_careers_page_linking_to_workday_resolves_to_it(self):
        html = '<a href="https://siemens.wd3.myworkdayjobs.com/en-US/External">Jobs</a>'
        source = jobtrack_watch._resolve_from_html(html, "https://jobs.example.com/")
        self.assertEqual(str(source), "workday:siemens.wd3.myworkdayjobs.com/siemens/External")


class TestTaleoAdapter(unittest.TestCase):
    SLUG = "lde.tbe.taleo.net/lde02/BARHOLD/37"

    def test_parses_requisitions_from_the_rss_feed(self):
        postings = jobtrack_watch.parse_taleo(_fixture_text("taleo.rss"), self.SLUG)
        self.assertEqual([p.id for p in postings], ["5211", "5119"])
        first = postings[0]
        self.assertEqual(first.title, "Strategy & Transformation Manager")
        self.assertEqual(first.location, "Portugal - Headquarters - Oporto, PT")
        self.assertEqual(first.posted, "2026-10-14")
        self.assertEqual(
            first.url,
            "https://lde.tbe.taleo.net/lde02/ats/careers/v2/viewRequisition?org=BARHOLD&cws=37&rid=5211",
        )
        self.assertIsNone(postings[1].posted)
        self.assertEqual(postings[1].location, "Italy - Stores")

    def test_invalid_xml_is_an_adapter_error(self):
        with self.assertRaises(jobtrack_watch.AdapterError):
            jobtrack_watch.parse_taleo("<html>maintenance", self.SLUG)

    def test_resolves_a_requisition_link(self):
        source = jobtrack_watch.resolve_url(
            "https://lde.tbe.taleo.net/lde02/ats/careers/v2/viewRequisition?org=BARHOLD&cws=37&rid=5168",
            fetch=False,
        )
        self.assertEqual(str(source), "taleo:" + self.SLUG)

    def test_taleo_enterprise_is_still_refused_by_name(self):
        with self.assertRaises(jobtrack_watch.ResolveError) as caught:
            jobtrack_watch.resolve_url(
                "https://acme.taleo.net/careersection/2/jobdetail.ftl?job=123", fetch=False
            )
        self.assertIn("Taleo Enterprise", str(caught.exception))


class TestTeamtailorAdapter(unittest.TestCase):
    def test_parses_roles_with_locations_and_remote_status(self):
        postings = jobtrack_watch.parse_teamtailor(
            _fixture_text("teamtailor.rss"), "careers.northwind.example"
        )
        self.assertEqual([p.title for p in postings], ["Head of AI Ops", "Cybersecurity Lead"])
        self.assertEqual(postings[0].location, "Porto, Portugal; Hybrid")
        self.assertEqual(postings[0].posted, "2026-10-05")
        self.assertEqual(postings[1].location, "Remote")
        self.assertEqual(postings[1].url, "https://careers.northwind.example/jobs/1002-cybersecurity-lead")

    def test_a_custom_domain_is_recognised_by_its_cdn(self):
        html = '<img src="https://images.teamtailor-cdn.com/images/s3/abc.jpg">'
        source = jobtrack_watch._resolve_from_html(html, "https://careers.northwind.example/jobs/1-x")
        self.assertEqual(str(source), "teamtailor:careers.northwind.example")

    def test_a_teamtailor_subdomain_resolves_without_fetching(self):
        source = jobtrack_watch.resolve_url("https://acme.teamtailor.com/jobs/1-pm", fetch=False)
        self.assertEqual(str(source), "teamtailor:acme.teamtailor.com")

    def test_fetch_reads_jobs_rss_on_the_career_host(self):
        with mock.patch.object(
            jobtrack_watch, "_get_html", return_value=_fixture_text("teamtailor.rss")
        ) as get:
            jobtrack_watch.fetch_postings(jobtrack_watch.Source("teamtailor", "careers.northwind.example"))
        self.assertEqual(get.call_args[0][0], "https://careers.northwind.example/jobs.rss")


class TestNotifyChannel(unittest.TestCase):
    def _result(self):
        posting = jobtrack_watch.Posting(id="1", title="Head of Strategy", url="https://x/1",
                                         location="Porto", posted=None)
        return jobtrack_watch.PollResult(company="Acme", source=jobtrack_watch.Source("ashby", "acme"),
                                         new=[posting])

    def test_console_prints_and_counts(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report = jobtrack_watch.notify([self._result()], channel="console")
        self.assertEqual(report.sent, 1)
        self.assertEqual(report.failed, [])
        self.assertIn("Head of Strategy", out.getvalue())

    def test_explicit_credentials_mean_telegram(self):
        sent = []
        with mock.patch.object(jobtrack_watch, "send_telegram",
                               side_effect=lambda m, t, c, opener=None: sent.append(m)):
            report = jobtrack_watch.notify([self._result()], credentials=("TOK", "1"))
        self.assertEqual(report.sent, 1)
        self.assertEqual(len(sent), 1)

    def test_channel_from_config(self):
        with mock.patch.object(jobtrack_watch.jobkit_config, "load",
                               return_value=jobtrack_watch.jobkit_config.Config(notify_channel="console")):
            with contextlib.redirect_stdout(io.StringIO()):
                report = jobtrack_watch.notify([self._result()])
        self.assertEqual(report.sent, 1)
