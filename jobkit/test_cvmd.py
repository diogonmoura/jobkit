from __future__ import annotations

import textwrap
import unittest

import cvmd

CV = """\
# Jane Doe
+351912345678 | jane.doe@example.com | [linkedin.com/in/janedoe](https://www.linkedin.com/in/janedoe) | Porto, Portugal

## Professional Profile
Strategy and operations leader with 10+ years.
Builds operating models.

## Professional Experience

### Head of Strategy | Leadership Team
Example Corp, Portugal | 2021 - Present
- **Team Leadership:** Leads a team of 6, across Portugal and Germany: in two time zones.
- Ran a cost programme that cut annual spend by 30% (#1 priority).

### Senior Consultant
Sample Consulting | 2016 - 2021

## Education

### Integrated Master in Mechanical Engineering
Faculty of Engineering, University of Porto, Porto | 2009 - 2014

## Skills
- **Core Skills:** Corporate strategy, Low-Code Automation (Power Automate, n8n), PMO
- **Languages:** Portuguese (Native), English (Fluent)

## Certifications
- PMP
- Prince2 Practitioner
"""


def parse(text):
    return cvmd.parse(textwrap.dedent(text))


class TestParse(unittest.TestCase):
    def setUp(self):
        self.doc = cvmd.parse(CV)

    def test_contact_line(self):
        self.assertEqual(self.doc["contact"], {
            "name": "Jane Doe",
            "phone": "+351912345678",
            "email": "jane.doe@example.com",
            "location": "Porto, Portugal",
            "links": [{"label": "linkedin.com/in/janedoe", "url": "https://www.linkedin.com/in/janedoe"}],
        })

    def test_phone_formats(self):
        for phone in ("(+351) 90 000 0000", "+351 912 345 678", "912-345-678", "+44 (0)20 7946 0000"):
            doc = cvmd.parse("# Jane\n{} | Porto\n".format(phone))
            self.assertEqual((doc["contact"]["phone"], doc["contact"]["location"]), (phone, "Porto"), phone)

    def test_profile_lines_are_joined(self):
        self.assertEqual(self.doc["profile"], "Strategy and operations leader with 10+ years. Builds operating models.")

    def test_experience_entries(self):
        first, second = self.doc["experience"]
        self.assertEqual(first["title"], "Head of Strategy | Leadership Team")
        self.assertEqual((first["company"], first["location"], first["dates"]),
                         ("Example Corp", "Portugal", "2021 - Present"))
        self.assertEqual(first["bullets"], [
            {"label": "Team Leadership", "text": "Leads a team of 6, across Portugal and Germany: in two time zones."},
            {"label": "", "text": "Ran a cost programme that cut annual spend by 30% (#1 priority)."},
        ])
        self.assertEqual((second["company"], second["location"], second["bullets"]), ("Sample Consulting", "", []))

    def test_education_splits_location_at_last_comma(self):
        school = self.doc["education"][0]
        self.assertEqual(school["degree"], "Integrated Master in Mechanical Engineering")
        self.assertEqual((school["school"], school["location"], school["dates"]),
                         ("Faculty of Engineering, University of Porto", "Porto", "2009 - 2014"))

    def test_skills_keep_commas_inside_parentheses(self):
        self.assertEqual(self.doc["skills"], {
            "Core Skills": ["Corporate strategy", "Low-Code Automation (Power Automate, n8n)", "PMO"],
            "Languages": ["Portuguese (Native)", "English (Fluent)"],
        })

    def test_other_headings_become_sections(self):
        self.assertEqual(self.doc["sections"], [{"title": "Certifications", "items": ["PMP", "Prince2 Practitioner"]}])

    def test_headings_match_case_insensitively(self):
        doc = cvmd.parse(CV.replace("## Professional Experience", "## professional experience"))
        self.assertEqual(len(doc["experience"]), 2)

    def test_windows_line_endings_and_bom(self):
        doc = cvmd.parse("\ufeff" + CV.replace("\n", "\r\n"))
        self.assertEqual(doc["contact"]["name"], "Jane Doe")
        self.assertEqual(doc["experience"][0]["dates"], "2021 - Present")

    def test_star_bullets_and_trailing_spaces(self):
        doc = cvmd.parse(CV.replace("- **Team", "* **Team").replace("30% (#1 priority).", "30% (#1 priority).   "))
        self.assertEqual(doc["experience"][0]["bullets"][1]["text"],
                         "Ran a cost programme that cut annual spend by 30% (#1 priority).")
        self.assertEqual(doc["experience"][0]["bullets"][0]["label"], "Team Leadership")

    def test_wrapped_bullet_continues(self):
        doc = cvmd.parse(CV.replace("- Ran a cost programme", "- Ran a cost\n  programme"))
        self.assertEqual(doc["experience"][0]["bullets"][1]["text"],
                         "Ran a cost programme that cut annual spend by 30% (#1 priority).")


class TestErrors(unittest.TestCase):
    def assertLineError(self, text, line, fragment):
        with self.assertRaises(cvmd.CvMdError) as ctx:
            cvmd.parse(text)
        self.assertEqual(ctx.exception.line, line, str(ctx.exception))
        self.assertIn(fragment, str(ctx.exception))

    def test_must_start_with_name(self):
        self.assertLineError("## Professional Profile\nText\n", 1, "# Your Name")

    def test_entry_needs_place_and_dates_line(self):
        bad = CV.replace("Sample Consulting | 2016 - 2021", "Sample Consulting 2016 - 2021")
        line = bad.splitlines().index("Sample Consulting 2016 - 2021") + 1
        self.assertLineError(bad, line, "Organisation, Location | Dates")

    def test_table_rejected(self):
        bad = CV.replace("- PMP", "| Cert | Year |")
        line = bad.splitlines().index("| Cert | Year |") + 1
        self.assertLineError(bad, line, "table")

    def test_deeper_heading_rejected(self):
        bad = CV.replace("- PMP", "#### Sub")
        line = bad.splitlines().index("#### Sub") + 1
        self.assertLineError(bad, line, "####")

    def test_text_before_first_entry_rejected(self):
        bad = CV.replace("## Professional Experience\n", "## Professional Experience\nSome intro text.\n")
        line = bad.splitlines().index("Some intro text.") + 1
        self.assertLineError(bad, line, "###")

    def test_duplicate_heading_rejected(self):
        bad = CV + "\n## Skills\n- **More:** x\n"
        self.assertLineError(bad, len(bad.splitlines()) - 1, "twice")

    def test_unrecognised_contact_items(self):
        self.assertLineError("# Jane\nPorto | Lisbon\n\n## Professional Profile\nx\n", 2, "contact")

    def test_skill_line_needs_label(self):
        bad = CV.replace("- **Languages:** Portuguese", "- Portuguese")
        line = bad.splitlines().index("- Portuguese (Native), English (Fluent)") + 1
        self.assertLineError(bad, line, "**Category:**")


if __name__ == "__main__":
    unittest.main()
