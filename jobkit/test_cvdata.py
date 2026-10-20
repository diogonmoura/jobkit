from __future__ import annotations

import os
import tempfile
import unittest

import cvdata

MINIMAL = {
    "contact": {"name": "Jane Doe", "email": "jane@example.com"},
    "profile": "Strategy lead.\n",
    "experience": [{"company": "Acme", "title": "Head of Strategy", "dates": "2021 - Present",
                    "bullets": [{"label": "Leadership", "text": "Leads 7."}, "Plain bullet"]}],
    "education": [{"school": "University of Porto", "degree": "MSc Management", "dates": 2015}],
    "skills": {"Strategy": ["M&A", "PMO"], "Tools": "n8n"},
}


class TestValidate(unittest.TestCase):
    def test_normalises(self):
        cv = cvdata.validate(MINIMAL)
        self.assertEqual(cv["profile"], "Strategy lead.")
        self.assertEqual(cv["contact"]["phone"], "")
        self.assertEqual(cv["contact"]["links"], [])
        self.assertEqual(cv["experience"][0]["location"], "")
        self.assertEqual(cv["experience"][0]["bullets"][1], {"label": "", "text": "Plain bullet"})
        self.assertEqual(cv["education"][0]["dates"], "2015")
        self.assertEqual(cv["education"][0]["bullets"], [])
        self.assertEqual(cv["skills"], [{"category": "Strategy", "items": ["M&A", "PMO"]},
                                        {"category": "Tools", "items": ["n8n"]}])
        self.assertEqual(cv["sections"], [])

    def test_reports_every_problem_with_its_path(self):
        doc = dict(MINIMAL)
        doc["contact"] = {}
        doc["experience"] = [{"company": "Acme", "bullets": [{"label": "x"}]}]
        doc["extra"] = 1
        with self.assertRaises(cvdata.CvError) as ctx:
            cvdata.validate(doc)
        message = str(ctx.exception)
        for fragment in ("contact.name: missing", "experience[0].title: missing",
                         "experience[0].dates: missing", "experience[0].bullets[0].text: missing",
                         "extra: unknown section"):
            self.assertIn(fragment, message)

    def test_empty_experience(self):
        doc = dict(MINIMAL, experience=[])
        with self.assertRaises(cvdata.CvError) as ctx:
            cvdata.validate(doc)
        self.assertIn("experience: must not be empty", str(ctx.exception))

    def test_wrong_type(self):
        doc = dict(MINIMAL, profile=["a", "b"])
        with self.assertRaises(cvdata.CvError) as ctx:
            cvdata.validate(doc)
        self.assertIn("profile: expected text", str(ctx.exception))

    def test_top_level_not_mapping(self):
        with self.assertRaises(cvdata.CvError):
            cvdata.validate(["a"])


class TestLoadCv(unittest.TestCase):
    def test_markdown_error_names_file_and_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cv.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# Jane\n\n## Skills\n| a | b |\n")
            with self.assertRaises(cvdata.CvError) as ctx:
                cvdata.load_cv(path)
        self.assertIn("cv.md", str(ctx.exception))
        self.assertIn("line 4", str(ctx.exception))

    def test_markdown_missing_sections_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cv.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# Jane\n")
            with self.assertRaises(cvdata.CvError) as ctx:
                cvdata.load_cv(path)
        self.assertIn("profile: missing", str(ctx.exception))

    def test_yaml_file_gets_a_migration_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cv.yaml")
            open(path, "w").close()
            with self.assertRaises(cvdata.CvError) as ctx:
                cvdata.load_cv(path)
        self.assertIn("Markdown", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
