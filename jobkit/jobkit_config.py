"""Per-user settings from jobkit.toml in the data repo.

Python 3.9 has no tomllib, so this reads the small TOML subset jobkit.toml
uses: [tables], key = "string" | 'string' | true | false | integer | [array of
those, possibly over several lines], and # comments. tomllib is used instead
when it exists, so a file that is valid TOML but outside the subset still
loads on 3.11+.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import jobkit_paths

CONFIG_NAME = "jobkit.toml"
CHANNELS = ("console", "telegram")


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    pdf_name: str = "CV.pdf"
    voice_rules: List[str] = field(default_factory=list)
    notify_channel: str = "console"
    telegram_env: str = "~/.claude/channels/telegram/.env"
    artifact_url: str = ""


_STRING = r'"(?:[^"\\]|\\.)*"|\'[^\']*\''
_ARRAY_ITEM = re.compile(r"\s*({}|true|false|-?\d+)\s*(,|$)".format(_STRING))


def _strip_comment(line: str) -> str:
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
        i += 1
    return line


def _depth(text: str) -> int:
    depth, quote, i = 0, None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    return depth


def _scalar(token: str, number: int) -> Any:
    if token.startswith('"'):
        try:
            return json.loads(token)
        except ValueError:
            raise ConfigError("jobkit.toml line {}: bad string {}".format(number, token)) from None
    if token.startswith("'"):
        return token[1:-1]
    if token in ("true", "false"):
        return token == "true"
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    raise ConfigError("jobkit.toml line {}: unsupported value {!r}".format(number, token))


def _value(text: str, number: int) -> Any:
    text = text.strip()
    if text.startswith("["):
        inner = text[1:-1] if text.endswith("]") else None
        if inner is None:
            raise ConfigError("jobkit.toml line {}: array is not closed".format(number))
        items, pos = [], 0
        inner = inner.strip()
        while pos < len(inner):
            match = _ARRAY_ITEM.match(inner, pos)
            if not match:
                raise ConfigError("jobkit.toml line {}: cannot read array {}".format(number, text))
            items.append(_scalar(match.group(1), number))
            pos = match.end()
            while pos < len(inner) and inner[pos] == " ":
                pos += 1
        return items
    return _scalar(text, number)


def parse_toml(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    table = data
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        number = i + 1
        line = _strip_comment(lines[i]).strip()
        i += 1
        if not line:
            continue
        header = re.fullmatch(r"\[([A-Za-z0-9_.-]+)\]", line)
        if header:
            table = data
            for part in header.group(1).split("."):
                table = table.setdefault(part, {})
            continue
        pair = re.fullmatch(r"([A-Za-z0-9_-]+)\s*=\s*(.+)", line)
        if not pair:
            raise ConfigError("jobkit.toml line {}: cannot read {!r}".format(number, line))
        key, value = pair.groups()
        while _depth(value) > 0:
            if i >= len(lines):
                raise ConfigError("jobkit.toml line {}: array is not closed".format(number))
            value += " " + _strip_comment(lines[i]).strip()
            i += 1
        table[key] = _value(value, number)
    return data


def _parse(text: str) -> Dict[str, Any]:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        return parse_toml(text)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError("jobkit.toml: {}".format(error)) from None


def load(path: Optional[str] = None) -> Config:
    path = path or jobkit_paths.data_file(CONFIG_NAME)
    if not os.path.exists(path):
        return Config()
    with open(path, encoding="utf-8") as handle:
        data = _parse(handle.read())
    user, voice = data.get("user", {}), data.get("voice", {})
    notify, tracker = data.get("notify", {}), data.get("tracker", {})
    config = Config(
        pdf_name=str(user.get("pdf_name", Config.pdf_name)),
        voice_rules=[str(rule) for rule in voice.get("rules", [])],
        notify_channel=str(notify.get("channel", Config.notify_channel)),
        telegram_env=str(notify.get("telegram_env", Config.telegram_env)),
        artifact_url=str(tracker.get("artifact_url", "")),
    )
    if config.notify_channel not in CHANNELS:
        raise ConfigError(
            "jobkit.toml: notify.channel must be one of {}, not {!r}".format(
                ", ".join(CHANNELS), config.notify_channel
            )
        )
    return config
