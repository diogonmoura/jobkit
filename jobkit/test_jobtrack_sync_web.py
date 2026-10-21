from __future__ import annotations

import datetime
import os
import shutil
import tempfile
import unittest

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
        self.states = {}
        self.clock = 0
        self.before_update = []
        self.fail_update = False

    def _tick(self):
        self.clock += 1
        return "t{}".format(self.clock)

    def pull_apps(self, user_id):
        return {(r["company"], r["role"]): (i, {k: v for k, v in r.items() if k != "updated_at"}, r["updated_at"])
                for i, r in self.rows.items()}

    def insert_app(self, user_id, app_id, record):
        self.rows[app_id] = dict(record, updated_at=self._tick())

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

    def get_sync_state(self, user_id, machine):
        return dict(self.states.get(machine, {}))

    def put_sync_state(self, user_id, machine, state):
        self.states[machine] = dict(state)

    @property
    def state(self):
        return self.states.get("mac", {})

    def web_edit(self, company, **fields):
        for row in self.rows.values():
            if row["company"] == company:
                row.update(fields)
                row["updated_at"] = self._tick()


class SyncWebTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.md = os.path.join(self.t, "applications.md")
        with open(self.md, "w", encoding="utf-8") as handle:
            handle.write(MD)
        self.web = FakeWeb()

    def tearDown(self):
        shutil.rmtree(self.t)

    def sync(self, md=None, machine="mac"):
        return js.sync_web(self.web, USER, md or self.md, machine)

    def apps(self, md=None):
        return {a.key: a for a in jobtrack.parse_log(jobtrack.read_log(md or self.md))}

    def edit_md(self, fn):
        text = fn(jobtrack.read_log(self.md))
        with open(self.md, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_first_sync_seeds_the_web_app_with_artifact_ids(self):
        result = self.sync()
        self.assertEqual(result["pushed"], 2)
        self.assertEqual(sorted(self.web.rows), ["acme-2026-09-01", "beta-2026-09-03"])
        self.assertIn(js.key_str(("Acme", "PM")), self.web.state)

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
        self.assertEqual(self.web.rows["beta-2026-09-03"]["status"], "rejected")

    def test_web_edit_between_pull_and_push_is_kept(self):
        self.sync()
        self.edit_md(lambda text: jobtrack.append_event(text, "Acme", "PM", datetime.date(2026, 9, 5), "call"))
        self.web.before_update.append(lambda w: w.web_edit(
            "Acme", last="2026-09-06", events=[{"d": "2026-09-06", "t": "email"}]))
        self.sync()
        self.assertEqual(sorted(e["t"] for e in self.web.rows["acme-2026-09-01"]["events"]), ["call", "email"])
        self.assertEqual(sorted(e.text for e in self.apps()[("Acme", "PM")].events), ["call", "email"])

    def test_gives_up_after_three_conflicts_and_changes_nothing(self):
        self.sync()
        state_before, md_before = dict(self.web.state), jobtrack.read_log(self.md)
        self.edit_md(lambda text: jobtrack.set_status(text, "Acme", "PM", "rejected"))
        md_before = jobtrack.read_log(self.md)
        self.web.before_update.extend([lambda w: w.web_edit("Acme", cv="x{}".format(w.clock))] * 3)
        with self.assertRaises(WebError) as ctx:
            self.sync()
        self.assertEqual(str(ctx.exception), "conflicting edits, try again")
        self.assertEqual(self.web.state, state_before)
        self.assertEqual(jobtrack.read_log(self.md), md_before)

    def test_failed_push_leaves_markdown_and_snapshot_alone(self):
        self.sync()
        self.web.web_edit("Acme", status="screening")
        self.edit_md(lambda text: jobtrack.set_status(text, "Beta", "CoS", "rejected"))
        state_before = dict(self.web.state)
        self.web.fail_update = True
        with self.assertRaises(WebError):
            self.sync()
        self.assertEqual(self.apps()[("Acme", "PM")].status, "applied")
        self.assertEqual(self.web.state, state_before)
        # A later web edit to the same field must not be undone once the push works again.
        self.web.fail_update = False
        self.web.web_edit("Acme", status="interview")
        self.sync()
        self.assertEqual(self.web.rows["acme-2026-09-01"]["status"], "interview")
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
        self.assertEqual(self.web.rows["acme-2026-09-01"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
