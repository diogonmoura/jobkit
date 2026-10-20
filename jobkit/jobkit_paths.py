"""Where a user's data lives: their data repo, which is the working directory.

JOBKIT_HOME overrides it. The scheduled poll sets it, because a LaunchAgent's
working directory is not something to rely on. State files were named
.jobtrack-* before jobkit; when only the old file exists it keeps being used,
so a migrated repo needs no renaming.
"""
from __future__ import annotations

import os
from typing import Dict

LEGACY_NAMES: Dict[str, str] = {
    ".jobkit-watch-state.json": ".jobtrack-watch-state.json",
    ".jobkit-follow.json": ".jobtrack-follow.json",
    ".jobkit-sync-state.json": ".jobtrack-sync-state.json",
    ".jobkit-watch.log": ".jobtrack-watch.log",
}


def data_root() -> str:
    return os.path.abspath(os.environ.get("JOBKIT_HOME") or os.getcwd())


def data_file(name: str) -> str:
    return os.path.join(data_root(), name)


def state_file(name: str) -> str:
    new = data_file(name)
    legacy = LEGACY_NAMES.get(name)
    if legacy and not os.path.exists(new) and os.path.exists(data_file(legacy)):
        return data_file(legacy)
    return new
