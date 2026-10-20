from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest

import jobtrack


GOOD_LOG = """# Applications

| Company | Role | Applied | CV | Status | Last contact | Posting |
|---|---|---|---|---|---|---|
| Northwind | Project Manager | 2026-07-24 | cv_northwind | rejected | 2026-08-01 | https://v.example/1 |
| Globex | Product Manager | 2026-07-27 | cv_globex | applied | 2026-07-27 | — |

## Events

### Northwind — Project Manager
- 2026-07-24 applied
- 2026-08-01 rejection email, no reason given
"""


class TestParseLog(unittest.TestCase):
    def test_parses_all_rows_in_order(self):
        apps = jobtrack.parse_log(GOOD_LOG)
        self.assertEqual([a.company for a in apps], ["Northwind", "Globex"])

    def test_parses_row_fields(self):
        northwind = jobtrack.parse_log(GOOD_LOG)[0]
        self.assertEqual(northwind.role, "Project Manager")
        self.assertEqual(northwind.applied, datetime.date(2026, 7, 24))
        self.assertEqual(northwind.cv, "cv_northwind")
        self.assertEqual(northwind.status, "rejected")
        self.assertEqual(northwind.last_contact, datetime.date(2026, 8, 1))
        self.assertEqual(northwind.posting, "https://v.example/1")

    def test_attaches_events_to_matching_row(self):
        northwind = jobtrack.parse_log(GOOD_LOG)[0]
        self.assertEqual(len(northwind.events), 2)
        self.assertEqual(northwind.events[1].date, datetime.date(2026, 8, 1))
        self.assertEqual(northwind.events[1].text, "rejection email, no reason given")

    def test_row_without_events_has_empty_list(self):
        globex = jobtrack.parse_log(GOOD_LOG)[1]
        self.assertEqual(globex.events, [])

    def test_tolerates_ragged_whitespace_and_misaligned_pipes(self):
        ragged = (
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "|Northwind|  Project Manager   |2026-07-24|cv_northwind|applied|2026-07-24|—|\n"
        )
        app = jobtrack.parse_log(ragged)[0]
        self.assertEqual(app.company, "Northwind")
        self.assertEqual(app.role, "Project Manager")

    def test_question_mark_role_is_allowed(self):
        text = (
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | ? | 2026-07-24 | cv_northwind | applied | 2026-07-24 | — |\n"
        )
        self.assertEqual(jobtrack.parse_log(text)[0].role, "?")

    def test_empty_role_is_a_parse_error(self):
        text = (
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind |  | 2026-07-24 | cv_northwind | applied | 2026-07-24 | — |\n"
        )
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack.parse_log(text)
        self.assertEqual(ctx.exception.line_no, 3)

    def test_bad_date_is_a_parse_error_naming_the_line(self):
        text = (
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | PM | 24/07/2026 | cv_northwind | applied | 2026-07-24 | — |\n"
        )
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack.parse_log(text)
        self.assertEqual(ctx.exception.line_no, 3)
        self.assertIn("YYYY-MM-DD", str(ctx.exception))

    def test_unknown_status_is_a_parse_error(self):
        text = (
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | PM | 2026-07-24 | cv_northwind | pending | 2026-07-24 | — |\n"
        )
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack.parse_log(text)
        self.assertIn("pending", str(ctx.exception))

    def test_orphaned_events_heading_is_a_parse_error(self):
        text = GOOD_LOG + "\n### Ghost Corp — Nobody\n- 2026-08-02 hello\n"
        with self.assertRaises(jobtrack.ParseError):
            jobtrack.parse_log(text)

    def test_duplicate_company_and_role_is_a_parse_error(self):
        text = (
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | PM | 2026-07-24 | cv_northwind | applied | 2026-07-24 | — |\n"
            "| Northwind | PM | 2026-07-25 | cv_northwind | applied | 2026-07-25 | — |\n"
        )
        with self.assertRaises(jobtrack.ParseError):
            jobtrack.parse_log(text)

    def test_wrong_column_count_is_a_parse_error(self):
        text = (
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | PM | 2026-07-24 |\n"
        )
        with self.assertRaises(jobtrack.ParseError):
            jobtrack.parse_log(text)

    def test_malformed_event_bullet_no_space_raises_parse_error(self):
        text = GOOD_LOG + "\n-2026-07-24 applied\n"
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack.parse_log(text)
        self.assertEqual(ctx.exception.line_no, 14)

    def test_malformed_event_bullet_bad_date_raises_parse_error(self):
        text = GOOD_LOG + "\n- not a date at all\n"
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack.parse_log(text)
        self.assertIn("event must be", str(ctx.exception))

    def test_horizontal_rule_in_events_section_is_skipped(self):
        text = GOOD_LOG + "\n---\n"
        apps = jobtrack.parse_log(text)
        self.assertEqual(len(apps), 2)

    def test_prose_line_in_events_section_is_skipped(self):
        text = GOOD_LOG + "\nSome prose text here\n"
        apps = jobtrack.parse_log(text)
        self.assertEqual(len(apps), 2)

    def test_bad_date_in_applications_md_mentions_applications_md_not_watchlist_md(self):
        text = (
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | PM | 24/07/2026 | cv_northwind | applied | 2026-07-24 | — |\n"
        )
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack.parse_log(text)
        self.assertIn("applications.md", str(ctx.exception))
        self.assertNotIn("watchlist.md", str(ctx.exception))

    def test_bad_date_in_applications_md_line_number_is_correct(self):
        text = (
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | PM | 24/07/2026 | cv_northwind | applied | 2026-07-24 | — |\n"
        )
        with self.assertRaises(jobtrack.ParseError) as ctx:
            jobtrack.parse_log(text)
        self.assertEqual(ctx.exception.line_no, 3)


def _app(status: str, last_contact: str, company: str = "Acme", role: str = "PM"):
    day = datetime.date.fromisoformat(last_contact)
    return jobtrack.Application(
        company=company,
        role=role,
        applied=day,
        cv="cv_acme",
        status=status,
        last_contact=day,
        posting="—",
    )


class TestFindStale(unittest.TestCase):
    TODAY = datetime.date(2026, 8, 3)

    def test_applied_is_not_stale_at_thirteen_days(self):
        apps = [_app("applied", "2026-07-21")]
        self.assertEqual(jobtrack.find_stale(apps, self.TODAY), [])

    def test_applied_is_not_stale_at_exactly_fourteen_days(self):
        apps = [_app("applied", "2026-07-20")]
        self.assertEqual(jobtrack.find_stale(apps, self.TODAY), [])

    def test_applied_is_stale_at_fifteen_days(self):
        apps = [_app("applied", "2026-07-19")]
        stale = jobtrack.find_stale(apps, self.TODAY)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].days_silent, 15)
        self.assertIn("follow up or mark ghosted", stale[0].reason)

    def test_active_is_not_stale_at_nine_days(self):
        apps = [_app("screening", "2026-07-25")]
        self.assertEqual(jobtrack.find_stale(apps, self.TODAY), [])

    def test_active_is_not_stale_at_exactly_ten_days(self):
        apps = [_app("screening", "2026-07-24")]
        self.assertEqual(jobtrack.find_stale(apps, self.TODAY), [])

    def test_active_is_stale_at_eleven_days(self):
        apps = [_app("interview", "2026-07-23")]
        stale = jobtrack.find_stale(apps, self.TODAY)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].days_silent, 11)
        self.assertIn("chase", stale[0].reason)
        # Wording is pinned to the spec's phrasing.
        self.assertIn("active process gone quiet for 11 days", stale[0].reason)

    def test_ack_uses_the_active_threshold(self):
        self.assertEqual(len(jobtrack.find_stale([_app("ack", "2026-07-23")], self.TODAY)), 1)

    def test_exempt_statuses_are_never_stale(self):
        apps = [
            _app("offer", "2026-01-01", company="A"),
            _app("rejected", "2026-01-01", company="B"),
            _app("withdrawn", "2026-01-01", company="C"),
            _app("ghosted", "2026-01-01", company="D"),
        ]
        self.assertEqual(jobtrack.find_stale(apps, self.TODAY), [])

    def test_sorted_most_overdue_first(self):
        apps = [
            _app("applied", "2026-07-19", company="Newer"),
            _app("applied", "2026-06-01", company="Older"),
        ]
        stale = jobtrack.find_stale(apps, self.TODAY)
        self.assertEqual([s.application.company for s in stale], ["Older", "Newer"])


class TestComputeStats(unittest.TestCase):
    def test_counts_and_response_rate(self):
        apps = [
            _app("applied", "2026-07-20", company="A"),
            _app("rejected", "2026-07-20", company="B"),
            _app("interview", "2026-07-20", company="C"),
            _app("ghosted", "2026-07-20", company="D"),
        ]
        stats = jobtrack.compute_stats(apps)
        self.assertEqual(stats.total, 4)
        self.assertEqual(stats.responded, 2)
        self.assertAlmostEqual(stats.response_rate, 0.5)

    def test_ghosted_counts_as_no_response(self):
        stats = jobtrack.compute_stats([_app("ghosted", "2026-07-20")])
        self.assertEqual(stats.responded, 0)
        self.assertAlmostEqual(stats.response_rate, 0.0)

    def test_by_status_includes_every_known_status(self):
        stats = jobtrack.compute_stats([_app("applied", "2026-07-20")])
        self.assertEqual(set(stats.by_status), set(jobtrack.STATUSES))
        self.assertEqual(stats.by_status["applied"], 1)
        self.assertEqual(stats.by_status["offer"], 0)

    def test_breakdown_by_cv_variant(self):
        first = _app("rejected", "2026-07-20", company="A")
        second = _app("applied", "2026-07-20", company="B")
        third = _app("applied", "2026-07-20", company="C")
        second.cv = "cv_other"
        third.cv = "cv_other"
        stats = jobtrack.compute_stats([first, second, third])
        self.assertEqual(stats.by_cv["cv_acme"], (1, 1))
        self.assertEqual(stats.by_cv["cv_other"], (0, 2))

    def test_empty_log_does_not_divide_by_zero(self):
        stats = jobtrack.compute_stats([])
        self.assertEqual(stats.total, 0)
        self.assertAlmostEqual(stats.response_rate, 0.0)
        self.assertIsNone(stats.median_days_to_first_response)

    def test_median_days_to_first_response_odd_count(self):
        apps = []
        for company, gap in (("A", 2), ("B", 6), ("C", 10)):
            app = _app("rejected", "2026-07-01", company=company)
            app.events = [
                jobtrack.Event(app.applied, "applied"),
                jobtrack.Event(app.applied + datetime.timedelta(days=gap), "reply"),
            ]
            apps.append(app)
        stats = jobtrack.compute_stats(apps)
        self.assertEqual(stats.median_days_to_first_response, 6)
        self.assertEqual(stats.median_sample_size, 3)

    def test_median_days_to_first_response_even_count(self):
        apps = []
        for company, gap in (("A", 2), ("B", 8)):
            app = _app("rejected", "2026-07-01", company=company)
            app.events = [jobtrack.Event(app.applied + datetime.timedelta(days=gap), "reply")]
            apps.append(app)
        stats = jobtrack.compute_stats(apps)
        self.assertEqual(stats.median_days_to_first_response, 5)

    def test_events_dated_on_the_applied_day_do_not_count_as_a_response(self):
        app = _app("applied", "2026-07-01")
        app.events = [jobtrack.Event(app.applied, "applied")]
        stats = jobtrack.compute_stats([app])
        self.assertIsNone(stats.median_days_to_first_response)
        self.assertEqual(stats.median_sample_size, 0)

    def test_time_to_response_ignores_events_on_applications_that_never_replied(self):
        """A 'sent follow-up myself' event on a still-applied row is not a response.

        Without this gate compute_stats reports 0% responded and a median
        time-to-first-response in the same breath, which contradicts itself.
        """
        app = _app("applied", "2026-07-01")
        app.events = [
            jobtrack.Event(app.applied, "applied"),
            jobtrack.Event(app.applied + datetime.timedelta(days=5), "sent follow-up myself"),
        ]
        stats = jobtrack.compute_stats([app])
        self.assertEqual(stats.responded, 0)
        self.assertIsNone(stats.median_days_to_first_response)
        self.assertEqual(stats.median_sample_size, 0)

    def test_response_rate_and_median_sample_can_never_contradict(self):
        """Zero responders must mean no median, and a median must mean responders."""
        mixed = []
        replied = _app("interview", "2026-07-01", company="Replied")
        replied.events = [
            jobtrack.Event(replied.applied, "applied"),
            jobtrack.Event(replied.applied + datetime.timedelta(days=4), "recruiter call"),
        ]
        chasing = _app("applied", "2026-07-01", company="Chasing")
        chasing.events = [
            jobtrack.Event(chasing.applied + datetime.timedelta(days=9), "chased them"),
        ]
        ghosted = _app("ghosted", "2026-07-01", company="Ghosted")
        ghosted.events = [
            jobtrack.Event(ghosted.applied + datetime.timedelta(days=20), "gave up"),
        ]
        mixed = [replied, chasing, ghosted]
        stats = jobtrack.compute_stats(mixed)
        self.assertEqual(stats.responded, 1)
        self.assertEqual(stats.median_sample_size, 1)
        self.assertEqual(stats.median_days_to_first_response, 4)
        self.assertEqual((stats.responded == 0), (stats.median_sample_size == 0))
        self.assertLessEqual(stats.median_sample_size, stats.responded)

    def test_median_sample_size_is_smaller_than_total_on_a_mixed_log(self):
        """A responder with no logged events still counts in the rate, not the median."""
        with_events = _app("rejected", "2026-07-01", company="Logged")
        with_events.events = [
            jobtrack.Event(with_events.applied + datetime.timedelta(days=6), "rejection email"),
        ]
        without_events = _app("offer", "2026-07-01", company="Unlogged")
        pending = _app("applied", "2026-07-01", company="Pending")
        stats = jobtrack.compute_stats([with_events, without_events, pending])
        self.assertEqual(stats.total, 3)
        self.assertEqual(stats.responded, 2)
        self.assertEqual(stats.median_sample_size, 1)
        self.assertNotEqual(stats.median_sample_size, stats.total)
        self.assertEqual(stats.median_days_to_first_response, 6)
        rendered = jobtrack.format_stats(stats)
        self.assertIn("2/3", rendered)
        self.assertIn("from 1 application", rendered)

    def test_small_sample_flag_set_below_thirty(self):
        self.assertTrue(jobtrack.compute_stats([_app("applied", "2026-07-20")]).small_sample)

    def test_small_sample_flag_clear_at_thirty(self):
        apps = [_app("applied", "2026-07-20", company="C{}".format(i)) for i in range(30)]
        self.assertFalse(jobtrack.compute_stats(apps).small_sample)


class TestWriters(unittest.TestCase):
    def test_set_status_changes_only_the_target_row(self):
        updated = jobtrack.set_status(GOOD_LOG, "Globex", "Product Manager", "screening")
        self.assertEqual(jobtrack.parse_log(updated)[1].status, "screening")
        self.assertIn("| Northwind | Project Manager | 2026-07-24 | cv_northwind | rejected", updated)

    def test_set_status_leaves_untouched_lines_byte_identical(self):
        updated = jobtrack.set_status(GOOD_LOG, "Globex", "Product Manager", "screening")
        before = [l for l in GOOD_LOG.splitlines() if "Globex" not in l]
        after = [l for l in updated.splitlines() if "Globex" not in l]
        self.assertEqual(before, after)

    def test_set_status_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            jobtrack.set_status(GOOD_LOG, "Globex", "Product Manager", "pending")

    def test_set_status_unknown_row_raises_not_found(self):
        with self.assertRaises(jobtrack.NotFound):
            jobtrack.set_status(GOOD_LOG, "Nobody", "Nothing", "applied")

    def test_append_application_adds_a_parseable_row(self):
        app = _app("applied", "2026-08-03", company="Initech", role="Strategy Director")
        updated = jobtrack.append_application(GOOD_LOG, app)
        apps = jobtrack.parse_log(updated)
        self.assertEqual([a.company for a in apps], ["Northwind", "Globex", "Initech"])

    def test_append_application_rejects_duplicates(self):
        app = _app("applied", "2026-08-03", company="Northwind", role="Project Manager")
        with self.assertRaises(jobtrack.Duplicate):
            jobtrack.append_application(GOOD_LOG, app)

    def test_append_event_creates_heading_for_row_without_events(self):
        updated = jobtrack.append_event(
            GOOD_LOG, "Globex", "Product Manager", datetime.date(2026, 8, 2), "phone screen booked"
        )
        globex = jobtrack.parse_log(updated)[1]
        self.assertEqual(len(globex.events), 1)
        self.assertEqual(globex.events[0].text, "phone screen booked")

    def test_append_event_appends_to_existing_heading_in_date_order(self):
        updated = jobtrack.append_event(
            GOOD_LOG, "Northwind", "Project Manager", datetime.date(2026, 7, 28), "recruiter call"
        )
        dates = [e.date for e in jobtrack.parse_log(updated)[0].events]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(dates), 3)

    def test_append_event_updates_last_contact(self):
        updated = jobtrack.append_event(
            GOOD_LOG, "Globex", "Product Manager", datetime.date(2026, 8, 2), "phone screen"
        )
        self.assertEqual(jobtrack.parse_log(updated)[1].last_contact, datetime.date(2026, 8, 2))

    def test_append_event_does_not_regress_last_contact_when_backfilling(self):
        """Backfilling an older event must not make the row look staler than it is."""
        updated = jobtrack.append_event(
            GOOD_LOG, "Globex", "Product Manager", datetime.date(2026, 8, 2), "phone screen"
        )
        backfilled = jobtrack.append_event(
            updated, "Globex", "Product Manager", datetime.date(2026, 7, 25), "applied online"
        )
        globex = jobtrack.parse_log(backfilled)[1]
        self.assertEqual(globex.last_contact, datetime.date(2026, 8, 2))
        self.assertEqual([e.date for e in globex.events],
                         [datetime.date(2026, 7, 25), datetime.date(2026, 8, 2)])

    def test_append_event_with_older_date_leaves_the_row_byte_identical(self):
        updated = jobtrack.append_event(
            GOOD_LOG, "Globex", "Product Manager", datetime.date(2026, 8, 2), "phone screen"
        )
        row = [l for l in updated.splitlines() if l.startswith("| Globex")]
        backfilled = jobtrack.append_event(
            updated, "Globex", "Product Manager", datetime.date(2026, 7, 25), "applied online"
        )
        self.assertEqual([l for l in backfilled.splitlines() if l.startswith("| Globex")], row)

    def test_append_event_equal_date_keeps_last_contact(self):
        updated = jobtrack.append_event(
            GOOD_LOG, "Globex", "Product Manager", datetime.date(2026, 7, 27), "nudge"
        )
        self.assertEqual(jobtrack.parse_log(updated)[1].last_contact, datetime.date(2026, 7, 27))

    def test_append_event_leaves_status_untouched(self):
        updated = jobtrack.append_event(
            GOOD_LOG, "Globex", "Product Manager", datetime.date(2026, 8, 2), "rejected by email"
        )
        self.assertEqual(jobtrack.parse_log(updated)[1].status, "applied")

    def test_append_event_unknown_row_raises_not_found(self):
        with self.assertRaises(jobtrack.NotFound):
            jobtrack.append_event(
                GOOD_LOG, "Nobody", "Nothing", datetime.date(2026, 8, 2), "hi"
            )

    def test_write_log_atomic_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "applications.md")
            jobtrack.write_log_atomic(path, GOOD_LOG)
            self.assertEqual(jobtrack.read_log(path), GOOD_LOG)

    def test_write_log_atomic_leaves_no_temp_files_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "applications.md")
            jobtrack.write_log_atomic(path, GOOD_LOG)
            self.assertEqual(os.listdir(tmp), ["applications.md"])

    def test_read_log_missing_file_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            jobtrack.read_log("/nonexistent/applications.md")


class TestTrailingNewlinePreservation(unittest.TestCase):
    """Regression coverage for Task 4 review finding 1: writers must reproduce
    the original text's trailing-newline state exactly, never adding or
    dropping one -- checked on the raw string, since parse_log tolerates
    blank lines and would silently pass even with a corrupted ending."""

    def test_append_event_new_heading_preserves_trailing_newline(self):
        updated = jobtrack.append_event(
            GOOD_LOG, "Globex", "Product Manager", datetime.date(2026, 8, 2), "phone screen booked"
        )
        self.assertTrue(updated.endswith("\n"))
        self.assertFalse(updated.endswith("\n\n"))

    def test_append_event_new_heading_preserves_absence_of_trailing_newline(self):
        no_trailing_newline = GOOD_LOG[:-1]
        self.assertFalse(no_trailing_newline.endswith("\n"))
        updated = jobtrack.append_event(
            no_trailing_newline,
            "Globex",
            "Product Manager",
            datetime.date(2026, 8, 2),
            "phone screen booked",
        )
        self.assertFalse(updated.endswith("\n"))

    def test_set_status_preserves_trailing_newline(self):
        updated = jobtrack.set_status(GOOD_LOG, "Globex", "Product Manager", "screening")
        self.assertTrue(updated.endswith("\n"))
        self.assertFalse(updated.endswith("\n\n"))

    def test_set_status_preserves_absence_of_trailing_newline(self):
        no_trailing_newline = GOOD_LOG[:-1]
        updated = jobtrack.set_status(no_trailing_newline, "Globex", "Product Manager", "screening")
        self.assertFalse(updated.endswith("\n"))

    def test_append_application_preserves_trailing_newline(self):
        app = _app("applied", "2026-08-03", company="Initech", role="Strategy Director")
        updated = jobtrack.append_application(GOOD_LOG, app)
        self.assertTrue(updated.endswith("\n"))
        self.assertFalse(updated.endswith("\n\n"))

    def test_append_application_preserves_absence_of_trailing_newline(self):
        no_trailing_newline = GOOD_LOG[:-1]
        app = _app("applied", "2026-08-03", company="Initech", role="Strategy Director")
        updated = jobtrack.append_application(no_trailing_newline, app)
        self.assertFalse(updated.endswith("\n"))


class TestFieldValidation(unittest.TestCase):
    """Regression coverage for Task 4 review finding 2: '|' and the heading
    separator must be rejected in company/role before any write happens,
    since either one produces a file that writes successfully but fails to
    parse back."""

    def test_append_application_rejects_pipe_in_company(self):
        app = _app("applied", "2026-08-03", company="Foo | Bar Inc", role="PM")
        with self.assertRaises(ValueError):
            jobtrack.append_application(GOOD_LOG, app)

    def test_append_application_rejects_pipe_in_role(self):
        app = _app("applied", "2026-08-03", company="Acme", role="PM | Ops")
        with self.assertRaises(ValueError):
            jobtrack.append_application(GOOD_LOG, app)

    def test_append_application_rejects_heading_separator_in_company(self):
        app = _app("applied", "2026-08-03", company="Acme — Sub", role="PM")
        with self.assertRaises(ValueError):
            jobtrack.append_application(GOOD_LOG, app)

    def test_append_event_rejects_pipe_in_company(self):
        with self.assertRaises(ValueError):
            jobtrack.append_event(GOOD_LOG, "Foo | Bar", "PM", datetime.date(2026, 8, 2), "note")

    def test_append_event_rejects_pipe_in_role(self):
        with self.assertRaises(ValueError):
            jobtrack.append_event(GOOD_LOG, "Acme", "PM | Ops", datetime.date(2026, 8, 2), "note")

    def test_append_event_rejects_heading_separator_in_role(self):
        with self.assertRaises(ValueError):
            jobtrack.append_event(GOOD_LOG, "Acme", "PM — Ops", datetime.date(2026, 8, 2), "note")

    def test_set_status_rejects_pipe_in_company(self):
        with self.assertRaises(ValueError):
            jobtrack.set_status(GOOD_LOG, "Foo | Bar", "PM", "applied")

    def test_append_application_rejects_empty_company(self):
        with self.assertRaises(ValueError):
            jobtrack.append_application(GOOD_LOG, _app("applied", "2026-08-03", company=""))

    def test_append_application_rejects_whitespace_only_role(self):
        app = _app("applied", "2026-08-03", company="Acme", role="   ")
        with self.assertRaises(ValueError):
            jobtrack.append_application(GOOD_LOG, app)

    def test_append_application_rejects_newline_in_company(self):
        app = _app("applied", "2026-08-03", company="Ac\nme", role="PM")
        with self.assertRaises(ValueError) as ctx:
            jobtrack.append_application(GOOD_LOG, app)
        self.assertIn("company", str(ctx.exception))

    def test_append_application_rejects_carriage_return_in_role(self):
        app = _app("applied", "2026-08-03", company="Acme", role="P\rM")
        with self.assertRaises(ValueError):
            jobtrack.append_application(GOOD_LOG, app)

    def test_append_application_rejects_pipe_in_posting(self):
        app = _app("applied", "2026-08-03", company="Acme", role="Dev")
        app.posting = "https://x.example/?a=1|b"
        with self.assertRaises(ValueError) as ctx:
            jobtrack.append_application(GOOD_LOG, app)
        self.assertIn("posting", str(ctx.exception))

    def test_append_application_rejects_pipe_in_cv(self):
        app = _app("applied", "2026-08-03", company="Acme", role="Dev")
        app.cv = "cv_a|b"
        with self.assertRaises(ValueError) as ctx:
            jobtrack.append_application(GOOD_LOG, app)
        self.assertIn("cv", str(ctx.exception))

    def test_append_application_rejects_newline_in_posting(self):
        app = _app("applied", "2026-08-03", company="Acme", role="Dev")
        app.posting = "https://x.example\nmore"
        with self.assertRaises(ValueError):
            jobtrack.append_application(GOOD_LOG, app)

    def test_append_application_rejects_the_header_literal_as_company(self):
        """A row whose company is 'Company' is written, then skipped by every reader."""
        app = _app("applied", "2026-08-03", company="Company", role="Dev")
        with self.assertRaises(ValueError) as ctx:
            jobtrack.append_application(GOOD_LOG, app)
        self.assertIn("Company", str(ctx.exception))

    def test_append_application_rejects_unknown_status(self):
        app = _app("pending", "2026-08-03", company="Acme", role="Dev")
        with self.assertRaises(ValueError):
            jobtrack.append_application(GOOD_LOG, app)

    def test_append_event_rejects_empty_note(self):
        with self.assertRaises(ValueError) as ctx:
            jobtrack.append_event(
                GOOD_LOG, "Northwind", "Project Manager", datetime.date(2026, 8, 3), ""
            )
        self.assertIn("event note", str(ctx.exception))

    def test_append_event_rejects_whitespace_only_note(self):
        with self.assertRaises(ValueError):
            jobtrack.append_event(
                GOOD_LOG, "Northwind", "Project Manager", datetime.date(2026, 8, 3), "   "
            )

    def test_append_event_rejects_newline_in_note(self):
        with self.assertRaises(ValueError) as ctx:
            jobtrack.append_event(
                GOOD_LOG,
                "Northwind",
                "Project Manager",
                datetime.date(2026, 8, 3),
                "a\n### Bogus — Role",
            )
        self.assertIn("event note", str(ctx.exception))

    def test_append_event_rejects_empty_company(self):
        with self.assertRaises(ValueError):
            jobtrack.append_event(GOOD_LOG, "", "PM", datetime.date(2026, 8, 3), "note")

    def test_set_status_rejects_empty_role(self):
        with self.assertRaises(ValueError):
            jobtrack.set_status(GOOD_LOG, "Northwind", "", "applied")

    def test_set_status_rejects_newline_in_company(self):
        with self.assertRaises(ValueError):
            jobtrack.set_status(GOOD_LOG, "Vo\nith", "Project Manager", "applied")

    def test_rejected_append_event_with_bad_note_does_not_touch_last_contact(self):
        """Validation runs before any mutation, so nothing is half-written."""
        for note in ("", "   ", "a\nb"):
            with self.assertRaises(ValueError):
                jobtrack.append_event(
                    GOOD_LOG, "Northwind", "Project Manager", datetime.date(2026, 8, 3), note
                )

    def test_rejected_append_application_leaves_file_on_disk_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "applications.md")
            jobtrack.write_log_atomic(path, GOOD_LOG)
            app = _app("applied", "2026-08-03", company="Foo | Bar", role="PM")
            with self.assertRaises(ValueError):
                jobtrack.append_application(jobtrack.read_log(path), app)
            self.assertEqual(jobtrack.read_log(path), GOOD_LOG)

    def test_rejected_append_event_leaves_file_on_disk_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "applications.md")
            jobtrack.write_log_atomic(path, GOOD_LOG)
            with self.assertRaises(ValueError):
                jobtrack.append_event(
                    jobtrack.read_log(path), "Foo | Bar", "PM", datetime.date(2026, 8, 2), "note"
                )
            self.assertEqual(jobtrack.read_log(path), GOOD_LOG)


CLI_LOG = """# Applications

| Company | Role | Applied | CV | Status | Last contact | Posting |
|---|---|---|---|---|---|---|
| Northwind | Project Manager | 2020-01-01 | cv_northwind | rejected | 2020-02-01 | https://v.example/1 |
| Globex | Product Manager | 2020-01-01 | cv_globex | applied | 2020-01-01 | — |

## Events

### Northwind — Project Manager
- 2020-01-01 applied
- 2020-02-01 rejection email, no reason given
"""


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "applications.md")
        jobtrack.write_log_atomic(self.path, CLI_LOG)

    def _run(self, *args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = jobtrack.main(list(args) + ["--file", self.path])
        return code, buffer.getvalue()

    def test_stale_json_lists_overdue_applications(self):
        code, out = self._run("stale", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(any(item["company"] == "Globex" for item in payload))

    def test_stale_json_excludes_exempt_statuses(self):
        _, out = self._run("stale", "--json")
        self.assertFalse(any(item["company"] == "Northwind" for item in json.loads(out)))

    def test_stats_json_has_expected_keys(self):
        _, out = self._run("stats", "--json")
        payload = json.loads(out)
        for key in ("total", "by_status", "response_rate", "by_cv", "small_sample"):
            self.assertIn(key, payload)

    def test_stats_text_warns_on_small_sample(self):
        _, out = self._run("stats")
        self.assertIn("too small", out.lower())

    def test_add_appends_a_row(self):
        code, _ = self._run(
            "add", "--company", "Initech", "--role", "Strategy Director",
            "--applied", "2026-07-30", "--cv", "cv_initech",
        )
        self.assertEqual(code, 0)
        apps = jobtrack.parse_log(jobtrack.read_log(self.path))
        self.assertEqual(apps[-1].company, "Initech")
        self.assertEqual(apps[-1].status, "applied")

    def test_add_duplicate_exits_nonzero_without_writing(self):
        before = jobtrack.read_log(self.path)
        code, _ = self._run("add", "--company", "Northwind", "--role", "Project Manager")
        self.assertNotEqual(code, 0)
        self.assertEqual(jobtrack.read_log(self.path), before)

    def test_add_to_read_only_directory_reports_clean_error_without_writing(self):
        readonly_dir = os.path.join(self.tmp.name, "readonly")
        os.mkdir(readonly_dir)
        path = os.path.join(readonly_dir, "applications.md")
        jobtrack.write_log_atomic(path, CLI_LOG)
        os.chmod(readonly_dir, 0o500)
        # Restore write permission before TemporaryDirectory cleanup runs, or
        # shutil.rmtree can't remove the directory's contents. addCleanup
        # runs LIFO, so this fires before self.tmp.cleanup() (registered in
        # setUp), regardless of how the test itself exits.
        self.addCleanup(os.chmod, readonly_dir, 0o700)

        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = jobtrack.main(
                [
                    "add", "--company", "Initech", "--role", "Strategy Director",
                    "--file", path,
                ]
            )
        self.assertNotEqual(code, 0)
        self.assertNotIn("Traceback", buffer.getvalue())
        self.assertEqual(jobtrack.read_log(path), CLI_LOG)

    def test_log_appends_event_and_moves_last_contact(self):
        code, _ = self._run(
            "log", "--company", "Globex", "--role", "Product Manager",
            "--date", "2026-08-02", "--text", "phone screen booked",
        )
        self.assertEqual(code, 0)
        globex = jobtrack.parse_log(jobtrack.read_log(self.path))[1]
        self.assertEqual(globex.last_contact, datetime.date(2026, 8, 2))
        self.assertEqual(globex.status, "applied")

    def _run_expecting_failure(self, *args):
        """Run a command that must fail cleanly, leaving a still-parseable file."""
        before = jobtrack.read_log(self.path)
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            with contextlib.redirect_stdout(io.StringIO()):
                code = jobtrack.main(list(args) + ["--file", self.path])
        self.assertNotEqual(code, 0, "command should have failed")
        self.assertNotIn("Traceback", buffer.getvalue())
        self.assertEqual(jobtrack.read_log(self.path), before)
        jobtrack.parse_log(jobtrack.read_log(self.path))  # still readable
        return buffer.getvalue()

    def test_log_with_empty_text_is_rejected_without_writing(self):
        err = self._run_expecting_failure(
            "log", "--company", "Northwind", "--role", "Project Manager", "--text", ""
        )
        self.assertIn("event note", err)

    def test_log_with_newline_in_text_is_rejected_without_writing(self):
        self._run_expecting_failure(
            "log", "--company", "Northwind", "--role", "Project Manager",
            "--text", "a\n### Bogus — Role",
        )

    def test_add_with_pipe_in_posting_is_rejected_without_writing(self):
        err = self._run_expecting_failure(
            "add", "--company", "Acme", "--role", "Dev", "--posting", "https://x?a=1|b"
        )
        self.assertIn("posting", err)

    def test_add_with_newline_in_company_is_rejected_without_writing(self):
        self._run_expecting_failure("add", "--company", "Ac\nme", "--role", "R")

    def test_add_with_header_literal_company_is_rejected_without_writing(self):
        self._run_expecting_failure("add", "--company", "Company", "--role", "Dev")

    def test_log_backfill_does_not_regress_last_contact(self):
        self._run(
            "log", "--company", "Globex", "--role", "Product Manager",
            "--date", "2026-08-02", "--text", "phone screen booked",
        )
        code, _ = self._run(
            "log", "--company", "Globex", "--role", "Product Manager",
            "--date", "2026-07-25", "--text", "applied online",
        )
        self.assertEqual(code, 0)
        globex = jobtrack.parse_log(jobtrack.read_log(self.path))[1]
        self.assertEqual(globex.last_contact, datetime.date(2026, 8, 2))
        self.assertEqual(len(globex.events), 2)

    def test_parse_error_exits_nonzero_with_line_number(self):
        jobtrack.write_log_atomic(
            self.path,
            "| Company | Role | Applied | CV | Status | Last contact | Posting |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Northwind | PM | nope | cv_northwind | applied | 2026-07-24 | — |\n",
        )
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = jobtrack.main(["stale", "--file", self.path])
        self.assertNotEqual(code, 0)
        self.assertIn("line 3", buffer.getvalue())

    def test_missing_file_reports_not_initialised(self):
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = jobtrack.main(["stale", "--file", "/nonexistent/applications.md"])
        self.assertNotEqual(code, 0)
        self.assertIn("not initialised", buffer.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
