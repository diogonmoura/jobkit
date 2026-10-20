"""Check that a machine and a data repo are ready for jobkit."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import List

import cvdata
import jobkit_config
import jobkit_launcher


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run_checks(root: str, which=shutil.which) -> List[Check]:
    checks = [Check("python", sys.version_info >= (3, 9), "Python {}.{} (3.9+ needed)".format(*sys.version_info[:2]))]
    for name, hint in (("typst", "brew install typst"), ("pdftotext", "brew install poppler")):
        found = which(name)
        checks.append(Check(name, bool(found), found or "missing; install with: {}".format(hint)))
    launcher = os.path.exists(jobkit_launcher.LAUNCHER_PATH)
    checks.append(Check("launcher", launcher, jobkit_launcher.LAUNCHER_PATH if launcher
                        else "missing; run: jobkit install-launcher"))
    config_path = os.path.join(root, jobkit_config.CONFIG_NAME)
    if not os.path.exists(config_path):
        checks.append(Check("data repo", False, "no jobkit.toml in {}; run /jobkit:init".format(root)))
        return checks
    try:
        jobkit_config.load(config_path)
        checks.append(Check("data repo", True, root))
    except jobkit_config.ConfigError as error:
        checks.append(Check("data repo", False, str(error)))
    missing = [name for name in ("cv.md", "applications.md", "watchlist.md")
               if not os.path.exists(os.path.join(root, name))]
    checks.append(Check("data files", not missing, "missing: " + ", ".join(missing) if missing else "ok"))
    if "cv.md" not in missing:
        try:
            cvdata.load_cv(os.path.join(root, "cv.md"))
            checks.append(Check("cv.md", True, "valid"))
        except cvdata.CvError as error:
            checks.append(Check("cv.md", False, str(error)))
    return checks
