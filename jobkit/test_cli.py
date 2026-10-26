from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import cli
import jobkit_doctor


def run(argv, home):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, {"JOBKIT_HOME": home}), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestGuard(unittest.TestCase):
    def test_data_command_outside_data_repo_refuses_and_writes_nothing(self):
        # Review Focus 4.
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(["add", "--company", "Acme", "--role", "PM"], tmp)
            self.assertEqual(code, 2)
            self.assertIn("/jobkit:init", err)
            self.assertEqual(os.listdir(tmp), [])

    def test_watch_outside_data_repo_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(["watch", "list"], tmp)
            self.assertEqual(code, 2)
            self.assertEqual(os.listdir(tmp), [])


class TestCommands(unittest.TestCase):
    def test_init_then_add_then_stats(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("jobkit_launcher.install", return_value="/x/launcher.py"):
            code, out, _ = run(["init", "--name", "Jane Doe"], tmp)
            self.assertEqual(code, 0)
            self.assertIn("created cv.md", out)
            code, out, _ = run(["add", "--company", "Acme", "--role", "PM", "--no-follow"], tmp)
            self.assertEqual(code, 0, out)
            code, out, _ = run(["stats", "--json"], tmp)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["total"], 1)

    def test_file_cv_uses_configured_name(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("jobkit_launcher.install", return_value="/x/launcher.py"):
            run(["init", "--name", "Jane Doe"], tmp)
            pdf = os.path.join(tmp, "tailored", "acme.pdf")
            with open(pdf, "wb") as handle:
                handle.write(b"%PDF")
            code, out, _ = run(["file-cv", "--company", "Acme", "--pdf", pdf, "--date", "2026-10-14"], tmp)
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(os.path.join(tmp, "Applications", "2026-10-14 - Acme", "CV_JaneDoe.pdf")))

    def test_render_reports_cv_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.md")
            with open(bad, "w", encoding="utf-8") as handle:
                handle.write("# Jane\n")
            code, _, err = run(["render", bad], tmp)
            self.assertEqual(code, 1)
            self.assertIn("profile: missing", err)
            self.assertNotIn("Traceback", err)

    def test_broken_local_connector_warns_and_carries_on(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("jobkit_launcher.install", return_value="/x/launcher.py"):
            run(["init", "--name", "Jane Doe"], tmp)
            with open(os.path.join(tmp, "connectors_local.py"), "w", encoding="utf-8") as handle:
                handle.write("import a_module_that_does_not_exist\n")
            code, out, err = run(["stats"], tmp)
        self.assertEqual(code, 0, err)
        self.assertIn("connectors_local.py", err)
        self.assertNotIn("Traceback", err)

    def test_plugin_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(["plugin-root"], tmp)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(out.strip(), "templates", "cv.typ")))


class TestDoctor(unittest.TestCase):
    def test_reports_missing_binaries_with_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = jobkit_doctor.run_checks(tmp, which=lambda name: None)
        by_name = {check.name: check for check in checks}
        self.assertFalse(by_name["typst"].ok)
        self.assertIn("brew install typst", by_name["typst"].detail)
        self.assertFalse(by_name["pdftotext"].ok)
        self.assertIn("brew install poppler", by_name["pdftotext"].detail)
        self.assertFalse(by_name["data repo"].ok)


if __name__ == "__main__":
    unittest.main()


TRACKER_MD = """# Applications

| Company | Role | Applied | CV | Status | Last contact | Posting |
|---|---|---|---|---|---|---|
| Acme | PM | 2026-09-01 | cv_acme | applied | 2026-09-01 | — |

## Events
"""


class TestWebAutoSync(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.toml('[tracker]\nbackend = "web"\nsupabase_url = "https://ref.supabase.co"\n')
        with open(os.path.join(self.home, "applications.md"), "w", encoding="utf-8") as handle:
            handle.write(TRACKER_MD)
        self.calls, self.error = [], None
        owner = mock.Mock()
        owner.owner_id.return_value = "u-1"
        owner.target = "https://ref.supabase.co"
        self.targets = []

        def fake_sync(client, user_id, md_path, machine, attempts=3, target=None, legacy_target=None):
            self.calls.append((user_id, os.path.basename(md_path)))
            self.targets.append((target, legacy_target))
            if self.error:
                raise self.error
            return {"md_changed": False, "pushed": 0, "records": 1}

        for target, kwargs in (("jobtrack_sync.sync_web", {"side_effect": fake_sync}),
                               ("jobkit_web.client_from_config", {"return_value": owner})):
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def toml(self, text):
        with open(os.path.join(self.home, "jobkit.toml"), "w", encoding="utf-8") as handle:
            handle.write(text)

    def add(self):
        return run(["add", "--company", "Beta", "--role", "CoS", "--no-follow"], self.home)

    def test_add_syncs_before_and_after(self):
        code, _, _ = self.add()
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [("u-1", "applications.md")] * 2)
        # The snapshot is checked against the client's site; a snapshot without one counts as supabase_url's.
        self.assertEqual(self.targets[0], ("https://ref.supabase.co", "https://ref.supabase.co"))

    def test_stats_syncs_only_before(self):
        run(["stats"], self.home)
        self.assertEqual(len(self.calls), 1)

    def test_render_and_artifact_backend_never_sync(self):
        run(["render", os.path.join(self.home, "missing.md")], self.home)
        self.toml('[tracker]\nartifact_url = "https://claude.ai/artifact/x"\n')
        self.add()
        self.assertEqual(self.calls, [])

    def test_offline_warns_once_and_the_command_still_runs(self):
        import jobkit_web

        self.error = jobkit_web.WebError("timed out")
        code, _, err = self.add()
        self.assertEqual(code, 0)
        self.assertEqual(err.count("warning: could not sync with the web app (timed out); "
                                   "using local applications.md"), 1)
        with open(os.path.join(self.home, "applications.md"), encoding="utf-8") as handle:
            self.assertIn("| Beta | CoS |", handle.read())

    def test_an_unexpected_sync_error_still_lets_the_command_run(self):
        self.error = TypeError("bad date")
        code, _, err = self.add()
        self.assertEqual(code, 0)
        self.assertIn("warning: could not sync with the web app (bad date)", err)

    def test_sync_web_command_reports_and_fails_loudly(self):
        code, out, _ = run(["sync", "web"], self.home)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["records"], 1)
        import jobkit_web

        self.error = jobkit_web.WebError("HTTP 401")
        code, _, err = run(["sync", "web"], self.home)
        self.assertEqual(code, 1)
        self.assertIn("error: HTTP 401", err)


class TestPushPostingsCommand(unittest.TestCase):
    def test_push_postings_uses_the_owner_and_reports(self):
        home = tempfile.mkdtemp()
        with open(os.path.join(home, "jobkit.toml"), "w", encoding="utf-8") as handle:
            handle.write('[tracker]\nbackend = "web"\nsupabase_url = "https://ref.supabase.co"\n')
        with open(os.path.join(home, "applications.md"), "w", encoding="utf-8") as handle:
            handle.write(TRACKER_MD)
        with open(os.path.join(home, "watchlist.md"), "w", encoding="utf-8") as handle:
            handle.write("# Watchlist\n\n| Company | Source | Keywords | Location | Added |\n|---|---|---|---|---|\n"
                         "| EDP | successfactors:jobs.edp.com | — | — | 2026-08-06 |\n")
        client = mock.Mock()
        client.owner_id.return_value = "u-1"
        result = {"pushed": 3, "boards": 1, "skipped_web_boards": 0, "errors": []}
        with mock.patch("jobkit_web.client_from_config", return_value=client), \
                mock.patch("jobtrack_watch.push_postings", return_value=result) as push:
            code, out, _ = run(["watch", "push-postings"], home)
        self.assertEqual(code, 0)
        self.assertEqual(push.call_args[0][2], "u-1")
        self.assertEqual(json.loads(out), result)
