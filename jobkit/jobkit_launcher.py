"""The stable entry point skills and the scheduled poll call.

Claude Code installs each plugin version into a new directory, so a path
remembered today breaks on the next update. The launcher looks the current
install up in Claude Code's plugin registry every time it runs.
"""
from __future__ import annotations

import os
import stat

LAUNCHER_PATH = os.path.expanduser("~/.jobkit/launcher.py")

SOURCE = '''#!/usr/bin/env python3
"""Run the installed jobkit plugin. Written by `jobkit install-launcher`.

Looks the current plugin install up in Claude Code's registry on every run,
so plugin updates need nothing here. JOBKIT_PLUGIN_ROOT overrides the lookup
(useful when developing jobkit itself).
"""
import json
import os
import sys

REGISTRY = os.path.expanduser("~/.claude/plugins/installed_plugins.json")


def plugin_root(registry=REGISTRY):
    override = os.environ.get("JOBKIT_PLUGIN_ROOT")
    if override:
        return override
    try:
        with open(registry, encoding="utf-8") as handle:
            plugins = json.load(handle).get("plugins", {})
    except (OSError, ValueError):
        plugins = {}
    installs = [entry for key, entries in plugins.items()
                if key.split("@")[0] == "jobkit" for entry in entries]
    if not installs:
        sys.exit("jobkit is not installed. In Claude Code run: /plugin install jobkit@jobkit")
    return max(installs, key=lambda entry: entry.get("lastUpdated", ""))["installPath"]


if __name__ == "__main__":
    cli = os.path.join(plugin_root(), "jobkit", "cli.py")
    os.execv(sys.executable, [sys.executable, cli] + sys.argv[1:])
'''


def install(path: str = LAUNCHER_PATH) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(SOURCE)
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
