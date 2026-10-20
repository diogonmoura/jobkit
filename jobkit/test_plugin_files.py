from __future__ import annotations

import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = ("init", "tailor-cv", "log-application", "follow-company", "build-connector", "sync-tracker")


class TestPluginFiles(unittest.TestCase):
    def test_manifests(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            plugin = json.load(handle)
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as handle:
            market = json.load(handle)
        self.assertEqual(plugin["name"], "jobkit")
        self.assertEqual(market["name"], "jobkit")
        self.assertEqual(market["plugins"][0]["name"], "jobkit")
        self.assertEqual(market["plugins"][0]["source"], "./")
        self.assertEqual(market["plugins"][0]["version"], plugin["version"])

    def test_skills_have_frontmatter_and_use_the_launcher(self):
        for name in SKILLS:
            with open(os.path.join(ROOT, "skills", name, "SKILL.md"), encoding="utf-8") as handle:
                text = handle.read()
            front = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", text)
            self.assertIsNotNone(front, name)
            self.assertEqual(front.group(1), name)
            if name != "init":
                self.assertIn("~/.jobkit/launcher.py", text, name)

    def test_tracker_page_has_no_personal_data(self):
        with open(os.path.join(ROOT, "artifact", "tracker.html"), encoding="utf-8") as handle:
            text = handle.read()
        for needle in ("Diogo", "Moura", "diogomoura", "Mercedes"):
            self.assertNotIn(needle, text)


    def test_no_owner_personal_data_in_code_or_tests(self):
        # The plugin is shared: the owner's name and application history stay
        # in their own data repo. Manifests, README, LICENSE and the docs may
        # name the author; this guard file itself has to spell the names.
        # JOBKIT_PRIVATE_NEEDLES adds comma-separated strings (email, phone) to
        # check for without committing them here.
        allowed = {"README.md", "LICENSE", "test_plugin_files.py"}
        extra = os.environ.get("JOBKIT_PRIVATE_NEEDLES", "")
        needles = ["Diogo", "Moura"] + [n.strip() for n in extra.split(",") if n.strip()]
        for folder, _, files in os.walk(ROOT):
            if any(part in folder for part in (".git", ".claude-plugin", "docs", ".superpowers", "__pycache__")):
                continue
            for name in files:
                if name in allowed:
                    continue
                with open(os.path.join(folder, name), encoding="utf-8", errors="ignore") as handle:
                    text = handle.read()
                for needle in needles:
                    self.assertFalse(needle in text, "{!r} in {}".format(needle, os.path.join(folder, name)))

if __name__ == "__main__":
    unittest.main()
