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
