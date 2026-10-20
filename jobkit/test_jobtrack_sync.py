import json, os, shutil, tempfile, unittest

import jobtrack
import jobtrack_sync as js

MD = """# Applications

| Company | Role | Applied | CV | Status | Last contact | Posting |
|---|---|---|---|---|---|---|
| Acme | PM | 2026-09-01 | cv_acme | applied | 2026-09-01 | — |

## Events
"""


def db_doc(d, ident, version=1, **over):
    data = {"company": "Acme", "role": "PM", "applied": "2026-09-01", "cv": "cv_acme",
            "status": "applied", "last": "2026-09-01", "posting": "", "events": []}
    data.update(over)
    with open(os.path.join(d, ident + ".json"), "w") as f:
        json.dump(data, f)
    VERSIONS[ident] = version


VERSIONS = {}


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.md = os.path.join(self.t, "applications.md")
        self.state = os.path.join(self.t, "state.json")
        self.db = os.path.join(self.t, "db"); os.mkdir(self.db)
        self.push = os.path.join(self.t, "push")
        open(self.md, "w").write(MD)

    def tearDown(self):
        shutil.rmtree(self.t)

    def sync(self):
        r = js.run_sync(self.md, self.db, self.state, self.push, VERSIONS)
        return r

    def apps(self):
        return {a.key: a for a in jobtrack.parse_log(open(self.md).read())}

    def test_md_only_row_is_pushed(self):
        r = self.sync()
        self.assertEqual(len(r["writes"]), 1)
        self.assertEqual(r["writes"][0]["doc_id"], "acme-2026-09-01")
        self.assertNotIn("if_version", r["writes"][0])

    def test_db_status_and_event_reach_markdown(self):
        self.sync(); js.commit(self.state)
        db_doc(self.db, "acme-2026-09-01", version=2, status="interview", last="2026-09-10",
               events=[{"d": "2026-09-10", "t": "status: interview"}])
        r = self.sync()
        a = self.apps()[("Acme", "PM")]
        self.assertEqual(a.status, "interview")
        self.assertEqual(a.last_contact.isoformat(), "2026-09-10")
        self.assertEqual(len(a.events), 1)
        self.assertEqual(r["writes"], [])

    def test_md_edit_reaches_db_with_version_pin(self):
        db_doc(self.db, "acme-2026-09-01", version=3)
        self.sync(); js.commit(self.state)
        text = open(self.md).read()
        open(self.md, "w").write(jobtrack.set_status(text, "Acme", "PM", "rejected"))
        r = self.sync()
        self.assertEqual(r["writes"][0]["if_version"], 3)
        self.assertEqual(json.load(open(r["writes"][0]["file_path"]))["status"], "rejected")

    def test_db_only_row_is_added_to_markdown(self):
        db_doc(self.db, "beta-2026-10-01", company="Beta", role="CoS", applied="2026-10-01",
               last="2026-10-01", cv="cv_beta", posting="https://x.test")
        self.sync()
        self.assertIn(("Beta", "CoS"), self.apps())

    def test_conflict_later_last_contact_wins(self):
        self.sync(); js.commit(self.state)
        text = open(self.md).read()
        open(self.md, "w").write(jobtrack.set_status(text, "Acme", "PM", "ack"))
        db_doc(self.db, "acme-2026-09-01", version=2, status="rejected", last="2026-09-20")
        self.sync()
        self.assertEqual(self.apps()[("Acme", "PM")].status, "rejected")

    def test_failed_push_is_retried_not_reverted(self):
        self.sync(); js.commit(self.state)
        text = open(self.md).read()
        open(self.md, "w").write(jobtrack.set_status(text, "Acme", "PM", "ack"))
        db_doc(self.db, "acme-2026-09-01", version=1)
        self.sync()  # push "fails": no commit
        r = self.sync()
        self.assertEqual(self.apps()[("Acme", "PM")].status, "ack")
        self.assertEqual(len(r["writes"]), 1)


if __name__ == "__main__":
    unittest.main()


class TestFollowingWrite(unittest.TestCase):
    def test_unchanged_companies_need_no_write_even_on_a_new_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = {"updated": "2026-10-14", "companies": [{"company": "EDP"}]}
            current = {"updated": "2026-10-01", "companies": [{"company": "EDP"}]}
            self.assertIsNone(js.following_write(doc, tmp, current, 3))

    def test_a_changed_list_is_written_pinned_to_the_exported_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = {"updated": "2026-10-14", "companies": [{"company": "Toggl"}]}
            entry = js.following_write(doc, tmp, {"companies": []}, 3)
            self.assertEqual(entry["if_version"], 3)
            self.assertEqual((entry["collection"], entry["doc_id"]), ("following", "all"))

    def test_a_first_write_creates_the_document_unpinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = js.following_write({"companies": []}, tmp, None, None)
            self.assertNotIn("if_version", entry)
