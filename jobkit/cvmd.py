"""Read a CV written in Markdown into the dict cvdata.validate checks.

The file follows one convention, so the engine can tell a job title from its
dates the way an ATS needs to:

    # Full Name
    phone | email | [label](url) | location
    ## Professional Profile          one paragraph (may wrap over lines)
    ## Professional Experience       ### Title, then "Organisation, Location | Dates",
    ## Education                     then "- **Label:** text" or "- text" bullets
    ## Skills                        "- **Category:** item, item, item"
    ## <any other heading>           "- item" lines or a paragraph, rendered after Skills

Markdown the template cannot render (tables, #### headings, numbered lists,
images, HTML, code blocks) is rejected with its line number instead of being
dropped, so nothing written in the file silently goes missing from the PDF.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

PROFILE = "professional profile"
EXPERIENCE = "professional experience"
EDUCATION = "education"
SKILLS = "skills"

_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_LABEL = re.compile(r"^\*\*(.+?)(?::\*\*|\*\*:)\s*(.*)$")
_LINK = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)$")
_EMAIL = re.compile(r"^[^\s@|]+@[^\s@|]+\.[^\s@|]+$")
_PHONE = re.compile(r"^[(+]{0,3}\d[\d\s().+-]{5,}$")
_UNSUPPORTED = (
    (re.compile(r"^\|"), "tables are not supported (they break ATS parsing); use '- ' bullets"),
    (re.compile(r"^#{4,}\s"), "#### headings are not supported; use ## for sections and ### for entries"),
    (re.compile(r"^\d+[.)]\s"), "numbered lists are not supported; use '- ' bullets"),
    (re.compile(r"^!\["), "images are not supported in an ATS-safe CV"),
    (re.compile(r"^<"), "HTML is not supported"),
    (re.compile(r"^(```|~~~)"), "code blocks are not supported"),
)
_KEYS = {EXPERIENCE: "experience", EDUCATION: "education"}
_ENTRY_LINE = "expected 'Organisation, Location | Dates' on the line under the ### title"


class CvMdError(ValueError):
    def __init__(self, line: int, message: str) -> None:
        super().__init__("line {}: {}".format(line, message))
        self.line = line


def split_items(text: str) -> List[str]:
    """Split "a, b (c, d), e" on the commas that are not inside parentheses."""
    items, depth, current = [], 0, ""
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]" and depth:
            depth -= 1
        if ch == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += ch
    items.append(current.strip())
    return [item for item in items if item]


def _bullet(text: str) -> Dict[str, str]:
    match = _LABEL.match(text)
    if match:
        return {"label": match.group(1).strip(), "text": match.group(2).strip()}
    return {"label": "", "text": text}


def _contact(pieces: List[str], number: int, contact: Dict[str, Any]) -> None:
    unknown = []
    for piece in pieces:
        link = _LINK.match(piece)
        if link and link.group(2).startswith("mailto:"):
            contact["email"] = link.group(2)[len("mailto:"):]
        elif link:
            contact["links"].append({"label": link.group(1), "url": link.group(2)})
        elif _EMAIL.match(piece):
            contact["email"] = piece
        elif _PHONE.match(piece):
            contact["phone"] = piece
        else:
            unknown.append(piece)
    if len(unknown) > 1:
        raise CvMdError(number, "contact line: could not tell what {} are; write 'phone | email | "
                                "[label](url) | location' with one location".format(" and ".join(
                                    repr(piece) for piece in unknown)))
    if unknown:
        contact["location"] = unknown[0]


def _place_and_dates(line: str, number: int) -> Dict[str, str]:
    if " | " not in line or _BULLET.match(line):
        raise CvMdError(number, _ENTRY_LINE)
    place, dates = line.rsplit(" | ", 1)
    org, sep, location = place.rpartition(", ")
    if not sep:
        org, location = place, ""
    return {"org": org.strip(), "location": location.strip(), "dates": dates.strip()}


def parse(text: str) -> Dict[str, Any]:
    lines = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    contact: Dict[str, Any] = {"name": "", "phone": "", "email": "", "location": "", "links": []}
    doc: Dict[str, Any] = {"contact": contact, "profile": "", "experience": [], "education": [],
                           "skills": {}, "sections": []}

    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index == len(lines) or not lines[index].startswith("# "):
        raise CvMdError(index + 1, "a CV starts with '# Your Name' on its first line")
    contact["name"] = lines[index][2:].strip()
    index += 1

    seen: Dict[str, int] = {}
    mode: Optional[str] = None  # None while still in the contact block
    profile: List[str] = []
    entry: Optional[Dict[str, Any]] = None  # the ### entry being read
    last: Optional[Dict[str, str]] = None   # the bullet a wrapped line continues
    section: Optional[Dict[str, Any]] = None
    skill: Optional[str] = None

    for number, raw in enumerate(lines[index:], start=index + 1):
        line = raw.strip()
        if not line:
            last = None if mode not in (EXPERIENCE, EDUCATION, SKILLS) else last
            continue
        for pattern, message in _UNSUPPORTED:
            if pattern.match(line):
                raise CvMdError(number, message)

        if line.startswith("## "):
            title = line[3:].strip()
            key = title.lower()
            if key in seen:
                raise CvMdError(number, "'## {}' appears twice (first on line {})".format(title, seen[key]))
            seen[key] = number
            mode, entry, last, skill = key, None, None, None
            section = None
            if key not in (PROFILE, EXPERIENCE, EDUCATION, SKILLS):
                section = {"title": title, "items": []}
                doc["sections"].append(section)
            continue
        if line.startswith("# "):
            raise CvMdError(number, "only the name uses '#'; sections use '##'")

        if mode is None:
            _contact([piece.strip() for piece in line.split(" | ")], number, contact)
            continue

        if line.startswith("### "):
            if mode not in (EXPERIENCE, EDUCATION):
                raise CvMdError(number, "### entries only go under Professional Experience or Education")
            entry = {"title": line[4:].strip(), "place": None, "bullets": [], "line": number}
            doc[_KEYS[mode]].append(entry)
            last = None
            continue

        bullet = _BULLET.match(line)
        continues = raw[:1] in (" ", "\t") and last is not None and not bullet

        if mode == PROFILE:
            if bullet:
                raise CvMdError(number, "write the profile as a paragraph, not bullets")
            profile.append(line)
        elif mode in (EXPERIENCE, EDUCATION):
            if entry is None:
                raise CvMdError(number, "start each entry with a '### Title' line")
            if entry["place"] is None:
                entry["place"] = _place_and_dates(line, number)
            elif continues:
                last["text"] += " " + line
            elif bullet:
                last = _bullet(bullet.group(1).strip())
                entry["bullets"].append(last)
            else:
                raise CvMdError(number, "expected a '- ' bullet under the entry")
        elif mode == SKILLS:
            if continues and skill is not None:
                doc["skills"][skill].extend(split_items(line))
                continue
            label = _LABEL.match(bullet.group(1).strip()) if bullet else None
            if not label:
                raise CvMdError(number, "write skills as '- **Category:** item, item'")
            skill = label.group(1).strip()
            if skill in doc["skills"]:
                raise CvMdError(number, "skill category '{}' appears twice".format(skill))
            doc["skills"][skill] = split_items(label.group(2))
            last = {"text": ""}
        else:
            if continues and section["items"]:
                section["items"][-1] += " " + line
            else:
                section["items"].append(bullet.group(1).strip() if bullet else line)
                last = {"text": ""}

    doc["profile"] = " ".join(profile)
    for kind, org_key, title_key in (("experience", "company", "title"), ("education", "school", "degree")):
        converted = []
        for entry in doc[kind]:
            if entry["place"] is None:
                raise CvMdError(entry["line"] + 1, _ENTRY_LINE)
            converted.append({title_key: entry["title"], org_key: entry["place"]["org"],
                              "location": entry["place"]["location"], "dates": entry["place"]["dates"],
                              "bullets": entry["bullets"]})
        doc[kind] = converted
    if not doc["sections"]:
        del doc["sections"]
    return doc
