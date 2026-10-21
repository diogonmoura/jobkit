from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import jobtrack_boards as b
import jobtrack_watch as w
import jobtrack_boards
import jobtrack_watch


def next_page(page_props: dict) -> str:
    return '<html><script id="__NEXT_DATA__" type="application/json">{}</script></html>'.format(
        json.dumps({"props": {"pageProps": page_props}})
    )


class TestRegistry(unittest.TestCase):
    def test_every_board_vendor_is_a_watch_vendor_with_a_fetcher(self):
        for vendor in b.VENDORS:
            self.assertIn(vendor, w.VENDORS)
            self.assertIn(vendor, w.ADAPTERS)
            self.assertIn(vendor, b.FETCHERS)

    def test_fetch_postings_dispatches_to_the_board_fetcher(self):
        posting = w.Posting("1", "T", "u", "", None)
        with mock.patch.dict(b.FETCHERS, {"join": lambda source, timeout: [posting]}):
            self.assertEqual(w.fetch_postings(w.Source("join", "acme")), [posting])


class TestBlockedSites(unittest.TestCase):
    def test_linkedin_and_revolut_are_refused_with_the_reason(self):
        for url, words in (
            ("https://www.linkedin.com/jobs/view/4478783662", "LinkedIn"),
            ("https://www.revolut.com/careers/position/x/", "Revolut"),
        ):
            with self.assertRaises(w.ResolveError) as caught:
                w.resolve_url(url, fetch=False)
            self.assertIn(words, str(caught.exception))
            board, note = w._unwatchable_reason(str(caught.exception))
            self.assertTrue(note.startswith("Posted on "))
            result = w.FollowResult("X", w.UNSUPPORTED, detail=str(caught.exception))
            self.assertFalse(result.needs_connector)


class TestOracle(unittest.TestCase):
    SLUG = "iamjgs.fa.ocs.oraclecloud.com/CX_2"
    PAYLOAD = {"items": [{"TotalJobsCount": 2, "requisitionList": [
        {"Id": "10449", "Title": "Strategy Lead", "PostedDate": "2026-10-12",
         "PrimaryLocation": "Lisbon, Portugal", "WorkplaceType": "Remote",
         "secondaryLocations": [{"Name": "Porto, Portugal"}]},
        {"Id": None, "Title": "No id"},
    ]}]}

    def test_parses_requisitions(self):
        postings, total = b.parse_oracle(self.PAYLOAD, self.SLUG)
        self.assertEqual(total, 2)
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0].location, "Lisbon, Portugal; Porto, Portugal; Remote")
        self.assertEqual(
            postings[0].url,
            "https://iamjgs.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/job/10449",
        )

    def test_an_empty_search_is_no_postings(self):
        self.assertEqual(b.parse_oracle({"items": []}, self.SLUG), ([], 0))

    def test_resolves_a_job_link(self):
        source = w.resolve_url(
            "https://iamjgs.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/job/10449",
            fetch=False,
        )
        self.assertEqual(str(source), "oracle:" + self.SLUG)


class TestCornerstone(unittest.TestCase):
    SLUG = "mytalent.csod.com/1"
    PAYLOAD = {"data": {"totalCount": 1, "requisitions": [
        {"requisitionId": 3863, "displayJobTitle": "AI/ML Engineer",
         "postingEffectiveDate": "10/12/2026", "locations": [{"city": "Porto", "country": "PT"}]},
    ]}}

    def test_parses_requisitions_and_reads_dates_in_the_site_culture(self):
        postings, total = b.parse_cornerstone(self.PAYLOAD, self.SLUG, "pt-PT")
        self.assertEqual(total, 1)
        self.assertEqual(postings[0].posted, "2026-12-10")
        self.assertEqual(postings[0].location, "Porto, PT")
        self.assertEqual(
            postings[0].url,
            "https://mytalent.csod.com/ux/ats/careersite/1/home/requisition/3863?c=mytalent",
        )
        us, _ = b.parse_cornerstone(self.PAYLOAD, self.SLUG, "en-US")
        self.assertEqual(us[0].posted, "2026-10-12")

    def test_reads_the_guest_session_from_the_career_site_page(self):
        page = '{"token":"abc.def","cloud":"https://uk.api.csod.com/","cultureID":16,"cultureName":"pt-PT"}'
        self.assertEqual(b._cornerstone_session(page), ("abc.def", "https://uk.api.csod.com/", 16, "pt-PT"))

    def test_a_page_without_a_token_is_an_adapter_error(self):
        with self.assertRaises(w.AdapterError):
            b._cornerstone_session("<html>maintenance</html>")

    def test_resolves_a_requisition_link(self):
        source = w.resolve_url(
            "https://mytalent.csod.com/ux/ats/careersite/1/home/requisition/3686?c=mytalent", fetch=False
        )
        self.assertEqual(str(source), "cornerstone:" + self.SLUG)


class TestJoin(unittest.TestCase):
    STATE = {"initialState": {"jobs": {"pagination": {"pageCount": 1}, "items": [
        {"id": 16522181, "idParam": "16792192-chief-of-staff-m-f-d", "title": "CHIEF OF STAFF (M/F/D)",
         "createdAt": "2026-07-31T15:49:17.111Z", "workplaceType": "REMOTE",
         "city": {"cityName": "Lisbon", "countryName": "Portugal"}},
    ]}}}

    def test_parses_jobs_from_page_data(self):
        postings, pages = b.parse_join({"props": {"pageProps": self.STATE}}, "poseidon-commscom")
        self.assertEqual(pages, 1)
        self.assertEqual(postings[0].title, "CHIEF OF STAFF (M/F/D)")
        self.assertEqual(postings[0].location, "Lisbon, Portugal; Remote")
        self.assertEqual(postings[0].posted, "2026-07-31")
        self.assertEqual(
            postings[0].url, "https://join.com/companies/poseidon-commscom/16792192-chief-of-staff-m-f-d"
        )

    def test_fetch_stops_after_the_last_page(self):
        with mock.patch.object(b, "_request", return_value=next_page(self.STATE)) as get:
            postings = b.fetch_join(w.Source("join", "poseidon-commscom"), 5)
        self.assertEqual(len(postings), 1)
        self.assertEqual(get.call_count, 1)

    def test_resolves_a_job_link(self):
        source = w.resolve_url(
            "https://join.com/companies/poseidon-commscom/16685972-chief-of-staff-m-f-d?pid=e652", fetch=False
        )
        self.assertEqual(str(source), "join:poseidon-commscom")


AVATURE_PAGE = """
<article><h3><a class="article__header__focusable"
  href="https://jobs.siemens-energy.com/en_US/jobs/FolderDetail/Strategy-Manager/304648">
  Strategy Manager (f/m/d)
</a><span class="hot-job-tag">Hot Job</span></h3>
<a class="button" href="https://jobs.siemens-energy.com/en_US/jobs/Login?folderId=304648">Apply</a></article>
<article><h3><a href="https://jobs.siemens-energy.com/en_US/jobs/FolderDetail/Elektroingeni-r/298018">Elektroingeni&oslash;r</a></h3></article>
"""


class TestAvature(unittest.TestCase):
    SLUG = "jobs.siemens-energy.com/en_US/jobs"

    def test_parses_roles_from_the_job_list(self):
        postings = b.parse_avature(AVATURE_PAGE, self.SLUG)
        self.assertEqual([p.id for p in postings], ["304648", "298018"])
        self.assertEqual(postings[0].title, "Strategy Manager (f/m/d)")
        self.assertEqual(postings[1].title, "Elektroingeniør")
        self.assertEqual(
            postings[0].url, "https://jobs.siemens-energy.com/en_US/jobs/FolderDetail/Strategy-Manager/304648"
        )
        self.assertEqual(postings[0].location, "")

    def test_fetch_pages_until_a_page_adds_nothing_new(self):
        pages = [AVATURE_PAGE, AVATURE_PAGE, ""]
        with mock.patch.object(b, "_request", side_effect=pages) as get:
            postings = b.fetch_avature(w.Source("avature", self.SLUG), 5, sleep=lambda s: None)
        self.assertEqual(len(postings), 2)
        self.assertEqual(get.call_count, 2)
        self.assertIn("folderOffset=20", get.call_args_list[1][0][0])

    def test_resolves_a_portal_page_by_its_fingerprint(self):
        source = w._resolve_from_html(
            '<link href="/portal/avature.css">',
            "https://jobs.siemens-energy.com/en_US/Jobs/FolderDetail?folderId=297038",
        )
        self.assertEqual(str(source), "avature:" + self.SLUG)


class TestBendingSpoons(unittest.TestCase):
    PROPS = {"list": [
        {"id": "63c548b3521886004f3daec0", "jobTitle": "Product manager", "status": "active",
         "availableAsRemoteInDefaultCountries": True,
         "officeLocations": [{"title": "Milan (Italy)"}, {"title": "London (UK)"}]},
        {"id": "x", "jobTitle": "Closed", "status": "closed", "officeLocations": []},
    ]}

    def test_parses_active_roles(self):
        postings = b.parse_bendingspoons({"props": {"pageProps": self.PROPS}}, "jobs.bendingspoons.com")
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0].location, "Milan (Italy); London (UK); Remote")
        self.assertEqual(postings[0].url, "https://jobs.bendingspoons.com/positions/63c548b3521886004f3daec0")

    def test_a_page_without_page_data_is_an_adapter_error(self):
        with mock.patch.object(b, "_request", return_value="<html></html>"):
            with self.assertRaises(w.AdapterError):
                b.fetch_bendingspoons(w.Source("bendingspoons", "jobs.bendingspoons.com"), 5)

    def test_resolves_a_position_link(self):
        source = w.resolve_url("https://jobs.bendingspoons.com/positions/6a7f2b30fbcf071796ff2923", fetch=False)
        self.assertEqual(str(source), "bendingspoons:jobs.bendingspoons.com")


class TestMantu(unittest.TestCase):
    PAYLOAD = {"hits": [
        {"id": 45456, "title": "Global AI Lead [F/M/X]", "brand_slug": "mantu",
         "locations": ["Portugal > Lisbon", "France > France"], "web_published_date": "2026-10-01 06:34:29"},
        {"id": 45338, "title": "Test Engineer", "brand_slug": "amaris-consulting", "locations": []},
    ]}

    def test_keeps_only_the_brand_and_formats_locations(self):
        postings = b.parse_mantu(self.PAYLOAD, "mantu")
        self.assertEqual([p.id for p in postings], ["45456"])
        self.assertEqual(postings[0].location, "Lisbon, Portugal; France")
        self.assertEqual(postings[0].posted, "2026-10-01")
        self.assertEqual(postings[0].url, "https://careers.mantu.com/brands/mantu/jobs/45456")

    def test_reads_the_search_settings_from_the_site_script(self):
        pages = [
            '<script src="https://careers.mantu.com/build/assets/app-1.js"></script>',
            'x=Ml({indexName:"prod_index",searchClient:hs("https://search.example","abc123",{})})',
        ]
        with mock.patch.object(b, "_request", side_effect=pages):
            self.assertEqual(b._mantu_search_config(5), ("https://search.example", "abc123", "prod_index"))

    def test_resolves_a_job_link(self):
        source = w.resolve_url("https://careers.mantu.com/brands/mantu/jobs/45456", fetch=False)
        self.assertEqual(str(source), "mantu:mantu")


ITJOBS_PAGE = """
<ul class="list-unstyled listing">
<li class="first"><a title="Northwind" href="/empresa/northwind"><img/></a>
 <div class="list-title"><a class="title" title="Strategy Manager" href="/oferta/518192/strategy-manager">Strategy Manager</a></div>
 <div class="list-name"><a title="Northwind" href="/empresa/northwind">Northwind</a></div>
 <div class="list-details"> Lisboa <!-- Full-time --> H&iacute;brido </div></li>
<li ><a href="/empresa/other">Other</a>
 <div class="list-title"><a class="title" href="/oferta/1/mentions-northwind">Mentions Northwind</a></div></li>
</ul><a href="?q=northwind&amp;page=2">2</a>
"""


class TestItjobs(unittest.TestCase):
    def test_keeps_only_rows_for_the_company(self):
        postings = b.parse_itjobs(ITJOBS_PAGE, "northwind")
        self.assertEqual([p.title for p in postings], ["Strategy Manager"])
        self.assertEqual(postings[0].location, "Lisboa Híbrido")
        self.assertEqual(postings[0].url, "https://www.itjobs.pt/oferta/518192/strategy-manager")

    def test_fetch_follows_pages_with_the_crawl_delay(self):
        delays = []
        with mock.patch.object(b, "_request", side_effect=[ITJOBS_PAGE, "<ul></ul>"]) as get:
            postings = b.fetch_itjobs(w.Source("itjobs", "northwind"), 5, sleep=delays.append)
        self.assertEqual(len(postings), 1)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(delays, [b.ITJOBS_CRAWL_DELAY])

    def test_resolves_a_company_page_and_an_offer_page(self):
        self.assertEqual(str(w.resolve_url("https://www.itjobs.pt/empresa/northwind", fetch=False)), "itjobs:northwind")
        source = b.resolve_itjobs(
            "https://www.itjobs.pt/oferta/515071/x", '<a href="/empresa/northwind">Northwind</a>'
        )
        self.assertEqual(str(source), "itjobs:northwind")


class TestLocalConnectors(unittest.TestCase):
    LOCAL = '''
from jobtrack_watch import Posting, Source

def fetch_acme(source, timeout):
    return [Posting(id="1", title="Head of Strategy", url="https://acme.example/jobs/1", location="Porto", posted=None)]

def resolve_acme(url):
    return Source("acme", "acme") if "acme.example" in url else None

FETCHERS = {"acme": fetch_acme}
URL_RESOLVERS = [resolve_acme]
'''

    def setUp(self):
        self.patches = [
            mock.patch.dict(jobtrack_boards.FETCHERS),
            mock.patch.dict(jobtrack_watch.ADAPTERS),
            mock.patch.object(jobtrack_watch, "VENDORS", jobtrack_watch.VENDORS),
            mock.patch.object(jobtrack_boards, "_LOCAL_URL_RESOLVERS", []),
            mock.patch.object(jobtrack_boards, "_LOCAL_PAGE_RESOLVERS", []),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(self.patches)])

    def test_no_file_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(jobtrack_boards.load_local_connectors(tmp), [])

    def test_registers_vendor_and_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "connectors_local.py"), "w", encoding="utf-8") as handle:
                handle.write(self.LOCAL)
            self.assertEqual(jobtrack_boards.load_local_connectors(tmp), ["acme"])
        self.assertIn("acme", jobtrack_watch.VENDORS)
        self.assertIn("acme", jobtrack_watch.ADAPTERS)
        source = jobtrack_boards.resolve_from_url("https://acme.example/careers")
        self.assertEqual((source.vendor, source.slug), ("acme", "acme"))
        postings = jobtrack_boards.FETCHERS["acme"](source, 5)
        self.assertEqual(postings[0].title, "Head of Strategy")

    def test_clash_with_builtin_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "connectors_local.py"), "w", encoding="utf-8") as handle:
                handle.write("FETCHERS = {'greenhouse': lambda s, t: []}\n")
            with self.assertRaises(ValueError):
                jobtrack_boards.load_local_connectors(tmp)


if __name__ == "__main__":
    unittest.main()
