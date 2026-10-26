from __future__ import annotations

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error

import jobkit_web
import jobtrack
import jobtrack_sync as js
from jobkit_web import WebError

USER = "u-1"
MD = """# Applications

| Company | Role | Applied | CV | Status | Last contact | Posting |
|---|---|---|---|---|---|---|
| Acme | PM | 2026-09-01 | cv_acme | applied | 2026-09-01 | — |
| Beta | CoS | 2026-09-03 | cv_beta | applied | 2026-09-03 | — |

## Events
"""


class FakeWeb:
    """jobkit_apps plus jobkit_sync_state in memory, behaving like jobkit_web.Client."""

    def __init__(self):
        self.rows = {}
        self.clock = 0
        self.before_update = []
        self.fail_update = False
        self.hidden_ids = set()  # rows pull_apps doesn't return, such as shortlisted "considering" jobs

    def _tick(self):
        self.clock += 1
        return "t{}".format(self.clock)

    TRACKER_FIELDS = ("company", "role", "applied", "cv", "status", "last", "posting", "events")

    def pull_apps(self, user_id):
        # Like jobkit_web.Client: tracker fields only; rows with web-only data and shortlisted rows noted.
        docs, self.web_only_keys, self.hidden_keys = {}, set(), {}
        for i, r in self.rows.items():
            key = (r["company"], r["role"])
            if r.get("status") == "considering":
                self.hidden_keys[key] = (i, r["updated_at"])
                continue
            if any(r.get(f) for f in ("cv_md", "posting_text", "answers", "job_id")):
                self.web_only_keys.add(key)
            docs[key] = (i, {f: r[f] for f in self.TRACKER_FIELDS}, r["updated_at"])
        return docs

    def insert_app(self, user_id, app_id, record):
        if app_id in self.rows or app_id in self.hidden_ids:
            raise WebError("HTTP 409", status=409)
        self.rows[app_id] = dict(record, updated_at=self._tick())

    def delete_app_if_unchanged(self, user_id, app_id, updated_at):
        row = self.rows.get(app_id)
        if row is None or row["updated_at"] != updated_at:
            return False
        del self.rows[app_id]
        return True

    def update_app_if_unchanged(self, user_id, app_id, updated_at, record):
        if self.fail_update:
            raise WebError("HTTP 503")
        if self.before_update:
            self.before_update.pop(0)(self)
        row = self.rows.get(app_id)
        if row is None or row["updated_at"] != updated_at:
            return False
        row.update(record)
        row["updated_at"] = self._tick()
        return True


    def web_edit(self, company, **fields):
        for row in self.rows.values():
            if row["company"] == company:
                row.update(fields)
                row["updated_at"] = self._tick()


class SyncWebBase(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.md = os.path.join(self.t, "applications.md")
        with open(self.md, "w", encoding="utf-8") as handle:
            handle.write(MD)
        self.web = FakeWeb()

    def tearDown(self):
        shutil.rmtree(self.t)

    def state_path(self, machine="mac"):
        return os.path.join(self.t, ".jobkit-web-sync-{}.json".format(machine))

    def state(self, machine="mac"):
        path = self.state_path(machine)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def apps(self, md=None):
        return {a.key: a for a in jobtrack.parse_log(jobtrack.read_log(md or self.md))}

    def edit_md(self, fn):
        text = fn(jobtrack.read_log(self.md))
        with open(self.md, "w", encoding="utf-8") as handle:
            handle.write(text)


class SyncWebTest(SyncWebBase):
    def sync(self, md=None, machine="mac"):
        return js.sync_web(self.web, USER, md or self.md, self.state_path(machine))

    def test_first_sync_seeds_the_web_app_with_role_ids(self):
        result = self.sync()
        self.assertEqual(result["pushed"], 2)
        self.assertEqual(sorted(self.web.rows), ["acme-pm-2026-09-01", "beta-cos-2026-09-03"])
        self.assertIn(js.key_str(("Acme", "PM")), self.state())

    def test_a_taken_id_gets_a_suffix_instead_of_blocking_every_sync(self):
        self.web.hidden_ids.add("acme-pm-2026-09-01")
        self.sync()
        self.assertIn("acme-pm-2026-09-01-2", self.web.rows)
        self.assertIn("beta-cos-2026-09-03", self.web.rows)

    def test_web_status_change_reaches_markdown(self):
        self.sync()
        self.web.web_edit("Acme", status="interview", last="2026-09-10",
                          events=[{"d": "2026-09-10", "t": "status: interview"}])
        result = self.sync()
        self.assertTrue(result["md_changed"])
        self.assertEqual(self.apps()[("Acme", "PM")].status, "interview")
        self.assertEqual(result["pushed"], 0)

    def test_markdown_edit_reaches_the_web_app(self):
        self.sync()
        self.edit_md(lambda text: jobtrack.set_status(text, "Beta", "CoS", "rejected"))
        self.sync()
        self.assertEqual(self.web.rows["beta-cos-2026-09-03"]["status"], "rejected")

    def test_web_edit_between_pull_and_push_is_kept(self):
        self.sync()
        self.edit_md(lambda text: jobtrack.append_event(text, "Acme", "PM", datetime.date(2026, 9, 5), "call"))
        self.web.before_update.append(lambda w: w.web_edit(
            "Acme", last="2026-09-06", events=[{"d": "2026-09-06", "t": "email"}]))
        self.sync()
        self.assertEqual(sorted(e["t"] for e in self.web.rows["acme-pm-2026-09-01"]["events"]), ["call", "email"])
        self.assertEqual(sorted(e.text for e in self.apps()[("Acme", "PM")].events), ["call", "email"])

    def test_gives_up_after_three_conflicts_and_changes_nothing(self):
        self.sync()
        state_before, md_before = dict(self.state()), jobtrack.read_log(self.md)
        self.edit_md(lambda text: jobtrack.set_status(text, "Acme", "PM", "rejected"))
        md_before = jobtrack.read_log(self.md)
        self.web.before_update.extend([lambda w: w.web_edit("Acme", cv="x{}".format(w.clock))] * 3)
        with self.assertRaises(WebError) as ctx:
            self.sync()
        self.assertEqual(str(ctx.exception), "conflicting edits, try again")
        self.assertEqual(self.state(), state_before)
        self.assertEqual(jobtrack.read_log(self.md), md_before)

    def test_failed_push_leaves_markdown_and_snapshot_alone(self):
        self.sync()
        self.web.web_edit("Acme", status="screening")
        self.edit_md(lambda text: jobtrack.set_status(text, "Beta", "CoS", "rejected"))
        state_before = dict(self.state())
        self.web.fail_update = True
        with self.assertRaises(WebError):
            self.sync()
        self.assertEqual(self.apps()[("Acme", "PM")].status, "applied")
        self.assertEqual(self.state(), state_before)
        # A later web edit to the same field must not be undone once the push works again.
        self.web.fail_update = False
        self.web.web_edit("Acme", status="interview")
        self.sync()
        self.assertEqual(self.web.rows["acme-pm-2026-09-01"]["status"], "interview")
        self.assertEqual(self.apps()[("Acme", "PM")].status, "interview")

    def test_web_edit_to_posting_and_cv_reaches_markdown_and_sticks(self):
        self.sync()
        self.web.web_edit("Acme", posting="https://jobs.example.com/1", cv="cv_acme_v2")
        self.sync()
        self.sync()
        acme = self.apps()[("Acme", "PM")]
        self.assertEqual((acme.posting, acme.cv), ("https://jobs.example.com/1", "cv_acme_v2"))
        row = self.web.rows["acme-pm-2026-09-01"]
        self.assertEqual((row["posting"], row["cv"]), ("https://jobs.example.com/1", "cv_acme_v2"))

    def test_web_delete_removes_the_row_and_its_events_from_markdown(self):
        self.edit_md(lambda text: jobtrack.append_event(text, "Acme", "PM", datetime.date(2026, 9, 2), "call"))
        self.sync()
        del self.web.rows["acme-pm-2026-09-01"]
        self.sync()
        self.assertNotIn(("Acme", "PM"), self.apps())
        self.assertNotIn("Acme", jobtrack.read_log(self.md))
        self.assertIn(("Beta", "CoS"), self.apps())
        self.assertNotIn("acme-pm-2026-09-01", self.web.rows)

    def test_markdown_delete_removes_the_row_from_the_web_app(self):
        self.sync()
        self.edit_md(lambda text: jobtrack.remove_application(text, "Beta", "CoS"))
        self.sync()
        self.assertNotIn("beta-cos-2026-09-03", self.web.rows)
        self.assertNotIn(("Beta", "CoS"), self.apps())

    def test_a_delete_never_wins_over_a_newer_edit_on_the_other_side(self):
        self.sync()
        del self.web.rows["acme-pm-2026-09-01"]
        self.edit_md(lambda text: jobtrack.set_status(text, "Acme", "PM", "screening"))
        self.sync()
        self.assertEqual(self.apps()[("Acme", "PM")].status, "screening")
        self.assertTrue(any(r["company"] == "Acme" for r in self.web.rows.values()))

    def test_renaming_in_markdown_never_deletes_a_web_row_with_web_only_data(self):
        self.sync()
        self.web.rows["acme-pm-2026-09-01"]["cv_md"] = "# Tailored CV"
        self.web.web_edit("Acme", cv="cv_acme")  # bumps updated_at like any web write
        self.sync()
        self.edit_md(lambda text: text.replace("| Acme | PM |", "| Acme | PMO |"))
        self.sync()
        self.assertEqual(self.web.rows["acme-pm-2026-09-01"]["cv_md"], "# Tailored CV")
        self.assertIn(("Acme", "PM"), self.apps())  # written back rather than lost
        self.assertIn(("Acme", "PMO"), self.apps())

    def test_applying_to_a_shortlisted_job_from_the_cli_marks_the_web_row_applied(self):
        self.web.rows["acme-shortlist"] = {"company": "Gamma", "role": "Lead", "applied": "2026-09-05", "cv": "",
                                           "status": "considering", "last": "2026-09-05", "posting": "",
                                           "events": [], "cv_md": "# CV", "updated_at": "t0"}
        self.edit_md(lambda text: jobtrack.append_application(text, jobtrack.Application(
            company="Gamma", role="Lead", applied=datetime.date(2026, 9, 6), cv="cv_gamma", status="applied",
            last_contact=datetime.date(2026, 9, 6), posting=jobtrack.PLACEHOLDER)))
        self.sync()
        gamma = [r for r in self.web.rows.values() if r["company"] == "Gamma"]
        self.assertEqual(len(gamma), 1)
        self.assertEqual((gamma[0]["status"], gamma[0]["cv"], gamma[0]["cv_md"]), ("applied", "cv_gamma", "# CV"))
        self.assertIn(("Gamma", "Lead"), self.apps())

    def test_a_row_moved_to_considering_stays_in_markdown_and_is_not_duplicated(self):
        self.sync()
        self.web.web_edit("Beta", status="considering")
        self.sync()
        self.assertIn(("Beta", "CoS"), self.apps())
        self.assertEqual(sorted(self.web.rows), ["acme-pm-2026-09-01", "beta-cos-2026-09-03"])

    def test_a_lost_push_does_not_revert_a_web_edit(self):
        # The daily Action syncs, but its commit of applications.md plus snapshot never lands. The next run
        # starts from the old file and the old snapshot together, so the web edit is still read as an edit.
        self.sync(machine="github-actions")
        with open(self.md, encoding="utf-8") as handle:
            md_before = handle.read()
        state_before = self.state("github-actions")
        self.web.web_edit("Acme", status="interview", last="2026-09-10")
        self.sync(machine="github-actions")
        with open(self.md, "w", encoding="utf-8") as handle:
            handle.write(md_before)
        with open(self.state_path("github-actions"), "w", encoding="utf-8") as handle:
            json.dump(state_before, handle)
        self.sync(machine="github-actions")
        self.assertEqual(self.web.rows["acme-pm-2026-09-01"]["status"], "interview")
        self.assertEqual(self.apps()[("Acme", "PM")].status, "interview")

    def test_a_stale_machine_does_not_undo_another_machines_edit(self):
        # The Mac and the GitHub Action each have their own applications.md and their own snapshot, both in
        # Supabase. A copy that is merely behind must read as behind, not as a fresh edit back to the old value.
        other = os.path.join(self.t, "other.md")
        shutil.copy(self.md, other)
        self.sync()
        self.sync(other, machine="github-actions")
        self.edit_md(lambda text: jobtrack.set_status(text, "Acme", "PM", "rejected"))
        self.sync()
        self.sync(other, machine="github-actions")
        self.assertEqual(self.apps(other)[("Acme", "PM")].status, "rejected")
        self.assertEqual(self.web.rows["acme-pm-2026-09-01"]["status"], "rejected")


SITE_A = "https://ref.supabase.co"
SITE_B = "https://jobs.example.com"


class SyncWebTargetTest(SyncWebBase):
    """The snapshot belongs to the site it was synced with; another site never reads it as a base."""

    def sync(self, target, legacy_target=None):
        return js.sync_web(self.web, USER, self.md, self.state_path(), target=target, legacy_target=legacy_target)

    def write_legacy_snapshot(self):
        # The format before targets: records only, as the Supabase client used to leave it.
        self.sync(SITE_A)
        records = {k: v for k, v in self.state().items() if k != "target"}
        with open(self.state_path(), "w", encoding="utf-8") as handle:
            json.dump(records, handle)

    def test_a_new_empty_site_gets_everything_and_markdown_keeps_it(self):
        self.sync(SITE_A)
        self.web = FakeWeb()  # the new site, nothing in it yet
        result = self.sync(SITE_B)
        self.assertEqual(sorted(self.apps()), [("Acme", "PM"), ("Beta", "CoS")])
        self.assertEqual(sorted(self.web.rows), ["acme-pm-2026-09-01", "beta-cos-2026-09-03"])
        self.assertEqual(result["pushed"], 2)

    def test_same_site_still_reads_a_web_delete_as_a_delete(self):
        self.sync(SITE_A)
        del self.web.rows["acme-pm-2026-09-01"]
        self.sync(SITE_A + "/")
        self.assertNotIn(("Acme", "PM"), self.apps())
        self.assertIn(("Beta", "CoS"), self.apps())

    def test_legacy_snapshot_matches_the_supabase_url_it_came_from(self):
        self.write_legacy_snapshot()
        del self.web.rows["acme-pm-2026-09-01"]
        self.sync(SITE_A, legacy_target="HTTPS://REF.supabase.co/")
        self.assertNotIn(("Acme", "PM"), self.apps())

    def test_legacy_snapshot_is_ignored_for_a_web_url(self):
        self.write_legacy_snapshot()
        self.web = FakeWeb()
        self.sync(SITE_B, legacy_target=SITE_A)
        self.assertEqual(sorted(self.apps()), [("Acme", "PM"), ("Beta", "CoS")])
        self.assertEqual(len(self.web.rows), 2)

    def test_snapshot_records_the_site_it_was_synced_with(self):
        self.write_legacy_snapshot()
        self.web = FakeWeb()
        self.sync(SITE_B + "/", legacy_target=SITE_A)
        state = self.state()
        self.assertEqual(state["target"], SITE_B)
        self.assertIn(js.key_str(("Acme", "PM")), state)


class SyncWebOverEngineTest(unittest.TestCase):
    """sync_web against the real EngineClient, with the Worker's answers scripted."""

    def setUp(self):
        import test_jobkit_web as tw

        self.tw = tw
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t)
        self.md = os.path.join(self.t, "applications.md")
        with open(self.md, "w", encoding="utf-8") as handle:
            handle.write(MD)
        self.state_path = os.path.join(self.t, ".jobkit-web-sync-mac.json")
        acme = js.md_record({a.key: a for a in jobtrack.parse_log(MD)}[("Acme", "PM")])
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump({"target": SITE_B, js.key_str(("Acme", "PM")): acme}, handle)
        self.acme_row = dict(acme, id="acme-pm-2026-09-01", updated_at="2026-10-21T10:00:00+00:00")

    def test_conflict_pulls_again_and_a_taken_id_gets_a_suffix(self):
        edited = jobtrack.set_status(jobtrack.read_log(self.md), "Acme", "PM", "rejected")
        with open(self.md, "w", encoding="utf-8") as handle:
            handle.write(edited)
        taken = urllib.error.HTTPError("u", 409, "x", {}, io.BytesIO(b'{"error":"exists"}'))
        opener = self.tw.FakeOpener(
            {"rows": [self.acme_row]}, {"updated": False},
            {"rows": [dict(self.acme_row, updated_at="t2")]}, {"updated": True},
            taken, {"inserted": True})
        client = jobkit_web.EngineClient(SITE_B, "tok", opener=opener)
        result = js.sync_web(client, USER, self.md, self.state_path, target=client.target)
        calls = [(m, u.replace(SITE_B + "/api/engine", "")) for m, u, _, _ in opener.requests]
        self.assertEqual(calls, [
            ("GET", "/apps"),
            ("PATCH", "/apps/acme-pm-2026-09-01?updated_at=2026-10-21T10%3A00%3A00%2B00%3A00"),
            ("GET", "/apps"),
            ("PATCH", "/apps/acme-pm-2026-09-01?updated_at=t2"),
            ("POST", "/apps"),
            ("POST", "/apps"),
        ])
        self.assertEqual(opener.requests[3][3]["status"], "rejected")
        self.assertEqual(opener.requests[4][3]["id"], "beta-cos-2026-09-03")
        self.assertEqual(opener.requests[5][3]["id"], "beta-cos-2026-09-03-2")
        self.assertNotIn("user_id", opener.requests[5][3])
        self.assertEqual(result["pushed"], 2)
        self.assertEqual(opener.answers, [])


if __name__ == "__main__":
    unittest.main()
