from __future__ import annotations

import os
import plistlib
import tempfile
import unittest

import jobkit_schedule as sched


class TestPlist(unittest.TestCase):
    def test_runs_launcher_with_jobkit_home(self):
        # Review Focus 5: the poll must find the data repo through JOBKIT_HOME.
        text = sched.plist_text("/Users/x/My CV & Jobs", launcher="/Users/x/.jobkit/launcher.py")
        data = plistlib.loads(text.encode("utf-8"))
        self.assertEqual(data["Label"], "com.jobkit.watch")
        self.assertEqual(data["ProgramArguments"],
                         ["/usr/bin/python3", "/Users/x/.jobkit/launcher.py", "watch", "poll"])
        self.assertEqual(data["EnvironmentVariables"]["JOBKIT_HOME"], "/Users/x/My CV & Jobs")
        self.assertEqual(data["WorkingDirectory"], "/Users/x/My CV & Jobs")
        self.assertEqual(data["StartCalendarInterval"], {"Hour": 8, "Minute": 30})
        self.assertEqual(data["StandardOutPath"], "/Users/x/My CV & Jobs/.jobkit-watch.log")

    def test_keeps_legacy_log_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, ".jobtrack-watch.log"), "w").close()
            self.assertEqual(sched.log_path(tmp), os.path.join(tmp, ".jobtrack-watch.log"))

    def test_cron_line_quotes_paths(self):
        line = sched.cron_line("/home/x/my jobs", launcher="/home/x/.jobkit/launcher.py")
        self.assertTrue(line.startswith("30 8 * * * "))
        self.assertIn("JOBKIT_HOME='/home/x/my jobs'", line)
        self.assertIn("watch poll", line)


class TestInstall(unittest.TestCase):
    def test_macos_writes_and_loads(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            message = sched.install("/data", agents_dir=tmp, run=lambda args, **kw: calls.append(args),
                                    system="Darwin")
            path = os.path.join(tmp, "com.jobkit.watch.plist")
            self.assertTrue(os.path.exists(path))
        self.assertEqual(calls[-1], ["launchctl", "load", path])
        self.assertIn("8:30", message)

    def test_linux_prints_cron(self):
        message = sched.install("/data", run=lambda *a, **k: None, system="Linux")
        self.assertIn("crontab -e", message)

    def test_uninstall(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "com.jobkit.watch.plist")
            open(path, "w").close()
            sched.uninstall(agents_dir=tmp, run=lambda args, **kw: calls.append(args), system="Darwin")
            self.assertFalse(os.path.exists(path))
        self.assertEqual(calls[0], ["launchctl", "unload", path])


if __name__ == "__main__":
    unittest.main()
