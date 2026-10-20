from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import atscheck
import cvdata
import cvrender

HAVE_TOOLS = bool(shutil.which("typst") and shutil.which("pdftotext"))
SAMPLE = os.path.join(cvrender.PLUGIN_ROOT, "samples", "cv.md")

CV = cvdata.validate({
    "contact": {"name": "Jane Doe"},
    "profile": "Strategy lead with ten years of experience in consulting.",
    "experience": [
        {"company": "Acme", "location": "Porto", "title": "Head of Strategy", "dates": "2021 - Present",
         "bullets": [{"label": "Leadership", "text": "Leads a team of 7 people."}]},
        {"company": "Beta", "title": "Consultant", "dates": "2016 - 2021", "bullets": ["Ran the PMO for twelve workstreams."]},
    ],
    "education": [{"school": "Porto", "degree": "MSc", "dates": "2015"}],
    "skills": {"Strategy": ["PMO"]},
})

GOOD = """Jane Doe
Professional Profile
Strategy lead with ten years of experience in
consulting.
Professional Experience
Head of Strategy
Acme, Porto | 2021 – Present
• Leadership: Leads a team of 7 people.
Consultant
Beta | 2016 – 2021
• Ran the PMO for twelve workstreams.
Education
MSc
Porto | 2015
Skills
Strategy: PMO
"""


class TestCheckText(unittest.TestCase):
    def test_clean_extraction_passes(self):
        self.assertEqual(atscheck.check_text(GOOD, CV), [])

    def test_split_entry_fails(self):
        # What the old two-column LaTeX layout produced: title and dates apart.
        broken = GOOD.replace("Acme, Porto | 2021 – Present", "Acme\nPorto\n2021 – Present")
        problems = atscheck.check_text(broken, CV)
        self.assertTrue(any("Acme, Porto | 2021 - Present" in p for p in problems), problems)

    def test_out_of_order_fails(self):
        swapped = GOOD.replace("Education\nMSc\nPorto | 2015\n", "").replace(
            "Professional Experience", "Education\nMSc\nPorto | 2015\nProfessional Experience")
        problems = atscheck.check_text(swapped, CV)
        self.assertTrue(any(p.startswith("out of order") for p in problems), problems)

    def test_duplicated_entry_fails(self):
        problems = atscheck.check_text(GOOD + "Beta | 2016 – 2021\n", CV)
        self.assertIn("duplicated: 'Beta | 2016 - 2021'", problems)

    def test_missing_tool_message(self):
        with self.assertRaises(atscheck.AtsError) as ctx:
            atscheck.extract_text("x.pdf", pdftotext="pdftotext-not-installed")
        self.assertIn("brew install poppler", str(ctx.exception))


@unittest.skipUnless(HAVE_TOOLS, "typst and pdftotext are needed (brew install typst poppler)")
class TestRenderedPdf(unittest.TestCase):
    def test_sample_passes(self):
        cv = cvdata.load_cv(SAMPLE)
        with tempfile.TemporaryDirectory() as tmp:
            pdf = cvrender.render(cv, os.path.join(tmp, "cv.pdf"))
            self.assertEqual(atscheck.check_pdf(pdf, cv), [])

    def test_markup_characters_and_accents_are_literal(self):
        # Review Focus 2: text must never be evaluated as Typst markup.
        tricky = "Cut costs 30% with *care*, _not_ $magic$, #1 @team <ops> \\ [x] in São João."
        cv = cvdata.validate({
            "contact": {"name": "Zoë Ångström", "email": "zoe@example.com"},
            "profile": tricky,
            "experience": [{"company": "Gestão & Co", "location": "São João", "title": "Head of C# Ops",
                            "dates": "2020 - 2024", "bullets": [{"label": "R&D", "text": tricky}]}],
            "education": [{"school": "Universidade", "degree": "Licenciatura", "dates": "2010"}],
            "skills": {"Línguas": ["Português", "English"]},
        })
        with tempfile.TemporaryDirectory() as tmp:
            pdf = cvrender.render(cv, os.path.join(tmp, "cv.pdf"))
            text = atscheck.extract_text(pdf)
            self.assertEqual(atscheck.check_pdf(pdf, cv), [])
        flat = atscheck.normalize(text)
        self.assertIn(atscheck.normalize(tricky), flat)
        self.assertIn("gestão & co, são joão | 2020 - 2024", flat)


if __name__ == "__main__":
    unittest.main()
