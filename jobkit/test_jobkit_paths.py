from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import jobkit_paths


class TestDataRoot(unittest.TestCase):
    def test_defaults_to_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JOBKIT_HOME", None)
            with mock.patch("os.getcwd", return_value=tmp):
                self.assertEqual(jobkit_paths.data_root(), os.path.abspath(tmp))

    def test_jobkit_home_overrides(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"JOBKIT_HOME": tmp}):
            self.assertEqual(jobkit_paths.data_file("applications.md"), os.path.join(tmp, "applications.md"))


class TestStateFile(unittest.TestCase):
    def test_new_name_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"JOBKIT_HOME": tmp}):
            self.assertEqual(
                jobkit_paths.state_file(".jobkit-follow.json"), os.path.join(tmp, ".jobkit-follow.json")
            )

    def test_legacy_name_kept_when_only_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"JOBKIT_HOME": tmp}):
            open(os.path.join(tmp, ".jobtrack-follow.json"), "w").close()
            self.assertEqual(
                jobkit_paths.state_file(".jobkit-follow.json"), os.path.join(tmp, ".jobtrack-follow.json")
            )

    def test_new_name_wins_when_both_exist(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"JOBKIT_HOME": tmp}):
            open(os.path.join(tmp, ".jobtrack-follow.json"), "w").close()
            open(os.path.join(tmp, ".jobkit-follow.json"), "w").close()
            self.assertEqual(
                jobkit_paths.state_file(".jobkit-follow.json"), os.path.join(tmp, ".jobkit-follow.json")
            )




class TestFollowPath(unittest.TestCase):
    def test_new_name_next_to_watchlist(self):
        import jobtrack

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(jobtrack._follow_path(os.path.join(tmp, "watchlist.md")),
                             os.path.join(tmp, ".jobkit-follow.json"))

    def test_legacy_name_kept(self):
        import jobtrack

        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, ".jobtrack-follow.json"), "w").close()
            self.assertEqual(jobtrack._follow_path(os.path.join(tmp, "watchlist.md")),
                             os.path.join(tmp, ".jobtrack-follow.json"))


class TestFollowUsesJobkitStateName(unittest.TestCase):
    def _state_path_used(self, tmp):
        import datetime
        import jobtrack
        import jobtrack_watch

        open(os.path.join(tmp, "watchlist.md"), "w").close()
        app = jobtrack.Application(company="Acme", role="PM", applied=datetime.date(2026, 10, 14), cv="acme",
                                   status="applied", last_contact=datetime.date(2026, 10, 14),
                                   posting="https://job-boards.greenhouse.io/acme/jobs/1")
        result = mock.Mock(warning="")
        result.describe.return_value = "Following Acme"
        with mock.patch.object(jobtrack_watch, "follow_posting", return_value=result) as follow, \
                mock.patch.object(jobtrack_watch, "record_follow"), \
                mock.patch.object(jobtrack_watch, "save_follows"), \
                mock.patch("sys.stdout"):
            jobtrack._follow_new_application(os.path.join(tmp, "applications.md"), app, datetime.date(2026, 10, 14))
        return follow.call_args[0][3]

    def test_fresh_repo_uses_jobkit_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._state_path_used(tmp), os.path.join(tmp, ".jobkit-watch-state.json"))

    def test_migrated_repo_keeps_legacy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, ".jobtrack-watch-state.json"), "w").close()
            self.assertEqual(self._state_path_used(tmp), os.path.join(tmp, ".jobtrack-watch-state.json"))


if __name__ == "__main__":
    unittest.main()
