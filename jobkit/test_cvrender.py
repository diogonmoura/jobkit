from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

import cvdata
import cvrender

HAVE_TOOLS = bool(shutil.which("typst") and shutil.which("pdftotext"))
SAMPLE = os.path.join(cvrender.PLUGIN_ROOT, "samples", "cv.md")


def pdf_text(path):
    return subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True, check=True).stdout


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
