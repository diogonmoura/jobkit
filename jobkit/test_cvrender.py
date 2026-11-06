from __future__ import annotations

import copy
import os
import shutil
import subprocess
import tempfile
import unittest

import atscheck
import cvdata
import cvrender

HAVE_TOOLS = bool(shutil.which("typst") and shutil.which("pdftotext"))
SAMPLE = os.path.join(cvrender.PLUGIN_ROOT, "samples", "cv.md")


def pdf_text(path):
    return subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True, check=True).stdout


def pdf_pages(path):
    """Each page as its non-empty lines. pdftotext ends every page with a form feed."""
    pages = pdf_text(path).split("\f")[:-1]
    return [[line.strip() for line in page.splitlines() if line.strip()] for page in pages]


class TestMissingTypst(unittest.TestCase):
    def test_clear_error(self):
        cv = cvdata.load_cv(SAMPLE)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(cvrender.RenderError) as ctx:
                cvrender.render(cv, os.path.join(tmp, "out.pdf"), typst="typst-not-installed")
        self.assertIn("brew install typst", str(ctx.exception))


@unittest.skipUnless(HAVE_TOOLS, "typst and pdftotext are needed (brew install typst poppler)")
class TestRender(unittest.TestCase):
    def test_sample_renders_single_column_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = cvrender.render_file(SAMPLE, os.path.join(tmp, "cv.pdf"))
            text = pdf_text(out)
        order = ["Jane Doe", "Professional Profile", "Professional Experience", "Head of Strategy",
                 "Example Corp, Portugal | 2021 – Present", "Team Leadership: Leads a team of 6",
                 "Senior Consultant", "Sample Consulting, Lisbon | 2016 – 2021", "Education",
                 "MSc Management", "University of Porto, Porto | 2014 – 2016", "Skills",
                 "Strategy:", "Languages"]
        flat = " ".join(text.split())
        positions = [flat.find(item) for item in order]
        self.assertNotIn(-1, positions, msg=flat)
        self.assertEqual(positions, sorted(positions), msg=flat)

    def test_default_output_next_to_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "acme.md")
            shutil.copy(SAMPLE, source)
            out = cvrender.render_file(source)
            self.assertEqual(out, os.path.join(tmp, "acme.pdf"))
            self.assertTrue(os.path.exists(out))


@unittest.skipUnless(HAVE_TOOLS, "typst and pdftotext are needed (brew install typst poppler)")
class TestExtraSections(unittest.TestCase):
    def render_lines(self, section):
        with open(SAMPLE, encoding="utf-8") as handle:
            source = handle.read() + "\n" + section
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cv.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(source)
            return [line for page in pdf_pages(cvrender.render_file(path)) for line in page]

    def test_three_part_items_render_as_entries(self):
        lines = self.render_lines("## Certifications\n"
                                  "- Certified Agilist | Agile Institute | 2022\n"
                                  "- PMP | PMI | 2019 - 2022\n")
        start = lines.index("Certified Agilist")
        self.assertEqual(lines[start:start + 4],
                         ["Certified Agilist", "Agile Institute | 2022", "PMP", "PMI | 2019 – 2022"])

    def test_any_other_item_keeps_the_section_on_one_line(self):
        lines = self.render_lines("## Certifications\n- PMP | PMI | 2019\n- First aid\n")
        self.assertIn("PMP | PMI | 2019, First aid", lines)

    def test_entries_pass_the_ats_check(self):
        cv = cvdata.load_cv(SAMPLE)
        cv["sections"].append({"title": "Certifications", "items": ["PMP | PMI | 2019"]})
        with tempfile.TemporaryDirectory() as tmp:
            pdf = cvrender.render(cv, os.path.join(tmp, "cv.pdf"))
            self.assertEqual(atscheck.check_pdf(pdf, cv), [])


@unittest.skipUnless(HAVE_TOOLS, "typst and pdftotext are needed (brew install typst poppler)")
class TestPageBreaks(unittest.TestCase):
    """Headings stay with what they introduce, wherever the page break falls.

    Each render makes the profile about one line longer, which walks everything
    below it down the page a line at a time, so every heading and entry title
    takes its turn at the bottom of page 1.
    """

    PROFILE_LINES = range(45, 70)
    WORDS_PER_LINE = 20

    def setUp(self):
        self.cv = cvdata.load_cv(SAMPLE)
        self.cv["education"][0]["bullets"] = [{"label": "", "text": "Thesis on operating models"}]
        self.cv["sections"].append({"title": "Certifications", "items": ["PMP | PMI | 2020"]})
        self.headings = ["Professional Experience", "Education", "Skills", "Languages", "Certifications"]
        self.titles = [job["title"] for job in self.cv["experience"]]
        self.titles += [school["degree"] for school in self.cv["education"]]
        self.places = [job["company"] + "," for job in self.cv["experience"]]
        self.places += [school["school"] + "," for school in self.cv["education"]]
        self.places.append("PMI |")

    def render_pages(self, profile_lines):
        cv = copy.deepcopy(self.cv)
        cv["profile"] = " ".join(["Filler"] * self.WORDS_PER_LINE * profile_lines)
        with tempfile.TemporaryDirectory() as tmp:
            return pdf_pages(cvrender.render(cv, os.path.join(tmp, "cv.pdf")))

    def is_heading(self, line):
        return line.lower() in [heading.lower() for heading in self.headings]

    def assert_breaks_clean(self, pages):
        for page in pages[:-1]:
            last = page[-1]
            self.assertFalse(self.is_heading(last), msg="heading alone at the page end: " + last)
            self.assertNotIn(last, self.titles, msg="entry title alone at the page end")
            self.assertFalse(last.startswith(tuple(self.places)), msg="entry without its bullets: " + last)
        for page in pages[1:]:
            self.assertFalse(page[0].startswith(tuple(self.places)),
                             msg="dates line split from its title: " + page[0])

    def test_no_heading_left_at_the_end_of_a_page(self):
        page_openers = set()
        for profile_lines in self.PROFILE_LINES:
            with self.subTest(profile_lines=profile_lines):
                pages = self.render_pages(profile_lines)
                page_openers.update(page[0].lower() for page in pages[1:])
                self.assert_breaks_clean(pages)
        # A first entry goes over with its heading, so only the second job opens a page itself.
        moved = {line.lower() for line in self.headings + ["Senior Consultant"]}
        self.assertLessEqual(moved, page_openers, msg="the sweep missed a heading or title")
