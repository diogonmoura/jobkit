#!/usr/bin/env python3
"""jobkit: CV tailoring and job search tracking, run from a user's data repo.

New commands live here; the tracker and board watching commands are
jobtrack.main's, and `sync` is jobtrack_sync.main's.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import atscheck  # noqa: E402
import cvdata  # noqa: E402
import cvrender  # noqa: E402
import jobkit_config  # noqa: E402
import jobkit_doctor  # noqa: E402
import jobkit_init  # noqa: E402
import jobkit_launcher  # noqa: E402
import jobkit_paths  # noqa: E402
import jobkit_schedule  # noqa: E402

OWN = ("init", "install-launcher", "plugin-root", "render", "ats-check", "file-cv", "doctor", "schedule")
# With the web app as the tracker backend, these sync first; the ones that write sync again afterwards.
TRACKER_READS = ("stale", "stats")
TRACKER_WRITES = ("add", "log")
NO_DATA_REPO_NEEDED = ("init", "install-launcher", "plugin-root", "render", "ats-check", "doctor")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobkit", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a data repo in the current folder")
    init.add_argument("--name", required=True, help="your full name, used for the PDF file name")
    sub.add_parser("install-launcher", help="write ~/.jobkit/launcher.py")
    sub.add_parser("plugin-root", help="print where the jobkit plugin is installed")
    render = sub.add_parser("render", help="render a CV (.md) to PDF")
    render.add_argument("file")
    render.add_argument("-o", "--output", default=None)
    ats = sub.add_parser("ats-check", help="check a PDF the way ATS parsers read it")
    ats.add_argument("pdf")
    ats.add_argument("--source", required=True, help="the .md CV the PDF was rendered from")
    file_cv = sub.add_parser("file-cv", help="copy a PDF into Applications/<date> - <Company>/")
    file_cv.add_argument("--company", required=True)
    file_cv.add_argument("--pdf", required=True)
    file_cv.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today")
    sub.add_parser("doctor", help="check this machine and data repo")
    schedule = sub.add_parser("schedule", help="run the board watch every morning")
    schedule.add_argument("action", choices=("install", "uninstall"))
    return parser


def _own(argv: List[str]) -> int:
    args = _parser().parse_args(argv)
    root = jobkit_paths.data_root()
    if args.command == "init":
        for relative, outcome in jobkit_init.scaffold(root, args.name):
            print("{} {}".format(outcome, relative))
        print("launcher at {}".format(jobkit_launcher.install()))
        return 0
    if args.command == "install-launcher":
        print(jobkit_launcher.install())
        return 0
    if args.command == "plugin-root":
        print(cvrender.PLUGIN_ROOT)
        return 0
    if args.command == "render":
        print(cvrender.render_file(args.file, args.output))
        return 0
    if args.command == "ats-check":
        problems = atscheck.check_pdf(args.pdf, cvdata.load_cv(args.source))
        for problem in problems:
            print(problem, file=sys.stderr)
        print("ATS check failed: {} problem(s)".format(len(problems)) if problems else "ATS check passed")
        return 1 if problems else 0
    if args.command == "file-cv":
        when = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
        pdf_name = jobkit_config.load().pdf_name
        print(jobkit_init.file_cv(args.pdf, args.company, root, pdf_name, when))
        return 0
    if args.command == "doctor":
        checks = jobkit_doctor.run_checks(root)
        for check in checks:
            print("{} {}: {}".format("ok  " if check.ok else "FAIL", check.name, check.detail))
        return 0 if all(check.ok for check in checks) else 1
    if args.command == "schedule":
        if args.action == "install":
            print(jobkit_schedule.install(root))
        else:
            print(jobkit_schedule.uninstall())
        return 0
    raise AssertionError(args.command)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else ""
    root = jobkit_paths.data_root()
    needs_repo = command not in NO_DATA_REPO_NEEDED and command not in ("", "-h", "--help")
    if needs_repo and not os.path.exists(os.path.join(root, jobkit_config.CONFIG_NAME)):
        print("{} is not a jobkit data repo (no jobkit.toml). Run /jobkit:init in Claude Code first, "
              "or cd to your data repo.".format(root), file=sys.stderr)
        return 2
    try:
        if needs_repo:
            import jobtrack_boards

            try:
                jobtrack_boards.load_local_connectors(root)
            except Exception as error:  # a half-written connector must not block add, log or the poll
                print("warning: ignoring {} ({}: {})".format(
                    jobtrack_boards.LOCAL_CONNECTORS, type(error).__name__, error), file=sys.stderr)
        if command in TRACKER_READS + TRACKER_WRITES:
            import jobtrack_sync

            # Offline before the command means offline after it too: one warning is enough.
            synced = jobtrack_sync.auto_sync_web()
            code = _dispatch(command, argv)
            if command in TRACKER_WRITES and synced:
                jobtrack_sync.auto_sync_web()
            return code
        return _dispatch(command, argv)
    except (cvdata.CvError, cvrender.RenderError, atscheck.AtsError,
            jobkit_config.ConfigError, ValueError, OSError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1



def _dispatch(command: str, argv: List[str]) -> int:
    if command in OWN or command in ("", "-h", "--help"):
        return _own(argv)
    if command == "sync":
        import jobtrack_sync

        return jobtrack_sync.main(argv[1:])
    import jobtrack

    return jobtrack.main(argv)


if __name__ == "__main__":
    sys.exit(main())
