from __future__ import annotations

import datetime
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

import jobkit_init
import jobkit_launcher
import jobtrack
import jobtrack_watch


class TestScaffold(unittest.TestCase):
    def test_creates_data_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = dict(jobkit_init.scaffold(tmp, "Jane Doe"))
            for name in ("cv.md", "jobkit.toml", "applications.md", "watchlist.md", "CLAUDE.md",
                         ".gitignore", "tailored/.gitkeep", "Applications/.gitkeep"):
                self.assertEqual(result[name], "created", name)
                self.assertTrue(os.path.exists(os.path.join(tmp, name)), name)
            with open(os.path.join(tmp, "jobkit.toml"), encoding="utf-8") as handle:
                self.assertIn('pdf_name = "CV_JaneDoe.pdf"', handle.read())

    def test_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "cv.md"), "w", encoding="utf-8") as handle:
                handle.write("mine")
            result = dict(jobkit_init.scaffold(tmp, "Jane Doe"))
            self.assertEqual(result["cv.md"], "kept")
            with open(os.path.join(tmp, "cv.md"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "mine")

    def test_fresh_tracker_accepts_first_application(self):
        # Review Focus 1.
        with tempfile.TemporaryDirectory() as tmp:
            jobkit_init.scaffold(tmp, "Jane Doe")
            with open(os.path.join(tmp, "applications.md"), encoding="utf-8") as handle:
                text = handle.read()
        day = datetime.date(2026, 10, 14)
        app = jobtrack.Application(company="Acme", role="Head of Strategy", applied=day, cv="acme",
                                   status="applied", last_contact=day, posting=jobtrack.PLACEHOLDER)
        updated = jobtrack.append_application(text, app)
        self.assertEqual(len(jobtrack.parse_log(updated)), 1)

    def test_fresh_watchlist_parses_and_accepts_first_row(self):
        # Review Focus 1.
        with tempfile.TemporaryDirectory() as tmp:
            jobkit_init.scaffold(tmp, "Jane Doe")
            with open(os.path.join(tmp, "watchlist.md"), encoding="utf-8") as handle:
                text = handle.read()
        self.assertEqual(jobtrack_watch.parse_watchlist(text).watches, [])
        updated = jobtrack_watch.add_watch(text, "Acme", jobtrack_watch.Source("greenhouse", "acme"),
                                           None, datetime.date(2026, 10, 14))
        self.assertEqual(len(jobtrack_watch.parse_watchlist(updated).watches), 1)

    def test_pdf_name(self):
        self.assertEqual(jobkit_init.pdf_name_for("Jane Q. Public"), "CV_JaneQPublic.pdf")
        self.assertEqual(jobkit_init.pdf_name_for("Zoë Ångström"), "CV_ZoeAngstrom.pdf")


class TestFileCv(unittest.TestCase):
    def test_copies_into_dated_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "acme.pdf")
            with open(pdf, "wb") as handle:
                handle.write(b"%PDF-1.7")
            dest = jobkit_init.file_cv(pdf, "Acme", tmp, "CV_JaneDoe.pdf", datetime.date(2026, 10, 14))
            self.assertEqual(dest, os.path.join(tmp, "Applications", "2026-10-14 - Acme", "CV_JaneDoe.pdf"))
            self.assertTrue(os.path.exists(dest))

    def test_rejects_path_in_company(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                jobkit_init.file_cv(__file__, "../x", tmp, "CV.pdf", datetime.date(2026, 10, 14))


class TestLauncher(unittest.TestCase):
    def _load(self, path):
        spec = importlib.util.spec_from_file_location("launcher_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_install_and_pick_newest(self):
        # Review Focus 5.
        with tempfile.TemporaryDirectory() as tmp:
            path = jobkit_launcher.install(os.path.join(tmp, "bin", "launcher.py"))
            self.assertTrue(os.access(path, os.X_OK))
            registry = os.path.join(tmp, "installed_plugins.json")
            with open(registry, "w", encoding="utf-8") as handle:
                json.dump({"version": 2, "plugins": {
                    "jobkit@jobkit": [
                        {"installPath": "/old", "lastUpdated": "2026-10-01T00:00:00Z"},
                        {"installPath": "/new", "lastUpdated": "2026-10-14T00:00:00Z"},
                    ],
                    "other@x": [{"installPath": "/other", "lastUpdated": "2026-12-01T00:00:00Z"}],
                }}, handle)
            launcher = self._load(path)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("JOBKIT_PLUGIN_ROOT", None)
                self.assertEqual(launcher.plugin_root(registry), "/new")
            with mock.patch.dict(os.environ, {"JOBKIT_PLUGIN_ROOT": "/dev/jobkit"}):
                self.assertEqual(launcher.plugin_root(registry), "/dev/jobkit")

    def test_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = jobkit_launcher.install(os.path.join(tmp, "launcher.py"))
            registry = os.path.join(tmp, "installed_plugins.json")
            with open(registry, "w", encoding="utf-8") as handle:
                json.dump({"plugins": {}}, handle)
            launcher = self._load(path)
            os.environ.pop("JOBKIT_PLUGIN_ROOT", None)
            with self.assertRaises(SystemExit):
                launcher.plugin_root(registry)


if __name__ == "__main__":
    unittest.main()
