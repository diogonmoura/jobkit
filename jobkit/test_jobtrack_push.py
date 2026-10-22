from __future__ import annotations

import datetime
import unittest
from unittest import mock

import jobtrack_watch as w

DAY = datetime.date(2026, 10, 1)


def watch(company, vendor, slug, keywords=None, location=None):
    return w.Watch(company=company, source=w.Source(vendor, slug), keywords=keywords, added=DAY, location=location)


def posting(pid, title, location="Lisbon, Portugal"):
    return w.Posting(id=pid, title=title, url="https://x.test/" + pid, location=location, posted=None)


class PushPostingsTest(unittest.TestCase):
    def setUp(self):
        self.watchlist = w.Watchlist(
            defaults=w.Filters(include=["strategy", "chief of staff"], exclude=["intern"], location=["Portugal"]),
            watches=[
                watch("EDP", "successfactors", "jobs.edp.com"),
                watch("Toggl", "ashby", "toggl"),
                watch("Natixis", "smartrecruiters", "NatixisInPortugal", keywords=["office"]),
            ],
        )
        self.client = mock.Mock()
        self.fetched = {
            "successfactors:jobs.edp.com": [posting("1", "Head of Strategy"), posting("2", "Strategy Intern"),
                                            posting("3", "Strategy Lead", location="Madrid, Spain")],
            "smartrecruiters:NatixisInPortugal": [posting("9", "CIO Office Manager")],
        }

    def fetch(self, source, timeout=None):
        return self.fetched[str(source)]

    def test_pushes_filtered_postings_from_boards_the_web_app_cannot_read(self):
        result = w.push_postings(self.watchlist, self.client, "u-1", fetch=self.fetch)
        rows = self.client.upsert_openings.call_args[0][1]
        self.assertEqual(self.client.upsert_openings.call_args[0][0], "u-1")
        self.assertEqual(sorted(r["external_key"] for r in rows),
                         ["smartrecruiters:NatixisInPortugal:9", "successfactors:jobs.edp.com:1"])
        edp = next(r for r in rows if r["company"] == "EDP")
        self.assertEqual(edp, {"external_key": "successfactors:jobs.edp.com:1", "company": "EDP",
                               "title": "Head of Strategy", "url": "https://x.test/1",
                               "location": "Lisbon, Portugal", "description": "", "source": "successfactors:jobs.edp.com"})
        self.assertEqual(result, {"pushed": 2, "boards": 2, "skipped_web_boards": 1, "errors": []})

    def test_one_failing_board_does_not_stop_the_rest(self):
        def fetch(source, timeout=None):
            if source.vendor == "successfactors":
                raise w.AdapterError("HTTP 503")
            return self.fetched[str(source)]

        result = w.push_postings(self.watchlist, self.client, "u-1", fetch=fetch)
        self.assertEqual(result["pushed"], 1)
        self.assertEqual(result["errors"], ["EDP: HTTP 503"])


if __name__ == "__main__":
    unittest.main()
