"""Run `watch poll` every morning: a LaunchAgent on macOS, a cron line elsewhere."""
from __future__ import annotations

import os
import platform
import shlex
import subprocess
from typing import Optional
from xml.sax.saxutils import escape

import jobkit_launcher
import jobkit_paths

LABEL = "com.jobkit.watch"


def log_path(root: str) -> str:
    legacy = os.path.join(root, jobkit_paths.LEGACY_NAMES[".jobkit-watch.log"])
    new = os.path.join(root, ".jobkit-watch.log")
    return legacy if os.path.exists(legacy) and not os.path.exists(new) else new


def plist_text(root: str, launcher: str = jobkit_launcher.LAUNCHER_PATH,
               python: str = "/usr/bin/python3", hour: int = 8, minute: int = 30) -> str:
    log = escape(log_path(root))
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{launcher}</string>
    <string>watch</string>
    <string>poll</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>JOBKIT_HOME</key>
    <string>{root}</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>{root}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
""".format(label=LABEL, python=escape(python), launcher=escape(launcher), root=escape(root),
           hour=hour, minute=minute, log=log)


def cron_line(root: str, launcher: str = jobkit_launcher.LAUNCHER_PATH, python: str = "python3") -> str:
    return "30 8 * * * cd {root} && JOBKIT_HOME={root} {python} {launcher} watch poll >> {log} 2>&1".format(
        root=shlex.quote(root), python=python, launcher=shlex.quote(launcher),
        log=shlex.quote(log_path(root)),
    )


def _agents_dir(agents_dir: Optional[str]) -> str:
    return agents_dir or os.path.expanduser("~/Library/LaunchAgents")


def install(root: str, agents_dir: Optional[str] = None, run=subprocess.run,
            system: Optional[str] = None) -> str:
    if (system or platform.system()) != "Darwin":
        return "Add this line with `crontab -e`:\n" + cron_line(root)
    folder = _agents_dir(agents_dir)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, LABEL + ".plist")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(plist_text(root))
    run(["launchctl", "unload", path], capture_output=True)
    run(["launchctl", "load", path], check=True)
    return "Scheduled `watch poll` daily at 8:30 for {} ({}).".format(root, path)


def uninstall(agents_dir: Optional[str] = None, run=subprocess.run, system: Optional[str] = None) -> str:
    if (system or platform.system()) != "Darwin":
        return "Remove the jobkit line with `crontab -e`."
    path = os.path.join(_agents_dir(agents_dir), LABEL + ".plist")
    if not os.path.exists(path):
        return "Nothing scheduled."
    run(["launchctl", "unload", path], capture_output=True)
    os.remove(path)
    return "Removed {}.".format(path)
