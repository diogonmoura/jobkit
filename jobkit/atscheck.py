"""Check a rendered CV the way an applicant tracking system reads it.

Workday, Greenhouse, iCIMS, Taleo and Lever extract PDF text in stream order,
which `pdftotext` without -layout mirrors. The check walks the CV source in
order and requires each heading, job title, "Company, Location | Dates" line
and the start of each bullet to appear in the extracted text in that same
order, with each entry line appearing once. Text is compared after Unicode
normalisation, dash and quote folding, whitespace collapsing and lowercasing,
so line wrapping and typographic dashes do not count as failures.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from typing import Any, Dict, List, Tuple

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
_QUOTES = {ord("\u2018"): "'", ord("\u2019"): "'", ord("\u201c"): '"', ord("\u201d"): '"'}


class AtsError(RuntimeError):
    pass


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip().lower()


def _lead(text: str, words: int = 6) -> str:
    return " ".join(text.split()[:words])


def _place(org: str, location: str) -> str:
    return "{}, {}".format(org, location) if location else org


def _bullet(bullet: Dict[str, str]) -> str:
    return "{}: {}".format(bullet["label"], bullet["text"]) if bullet["label"] else bullet["text"]


def expected(cv: Dict[str, Any]) -> List[Tuple[str, bool]]:
    items: List[Tuple[str, bool]] = [
        (cv["contact"]["name"], False),
        ("Professional Profile", False),
        (_lead(cv["profile"], 8), False),
        ("Professional Experience", False),
    ]
    for job in cv["experience"]:
        items.append((job["title"], False))
        items.append((_place(job["company"], job["location"]) + " | " + job["dates"], True))
        items.extend((_lead(_bullet(b)), False) for b in job["bullets"])
    items.append(("Education", False))
    for school in cv["education"]:
        items.append((school["degree"], False))
        items.append((_place(school["school"], school["location"]) + " | " + school["dates"], True))
        items.extend((_lead(_bullet(b)), False) for b in school["bullets"])
    items.append(("Skills", False))
    items.extend((skill["category"] + ":", False) for skill in cv["skills"])
    items.extend((section["title"], False) for section in cv["sections"])
    return items


def check_text(text: str, cv: Dict[str, Any]) -> List[str]:
    haystack = normalize(text)
    problems: List[str] = []
    position = 0
    for snippet, unique in expected(cv):
        needle = normalize(snippet)
        found = haystack.find(needle, position)
        if found < 0:
            kind = "out of order" if needle in haystack else "missing"
            problems.append("{}: {!r}".format(kind, snippet))
            continue
        if unique and haystack.count(needle) > 1:
            problems.append("duplicated: {!r}".format(snippet))
        position = found + len(needle)
    return problems


def extract_text(pdf: str, pdftotext: str = "pdftotext") -> str:
    binary = shutil.which(pdftotext)
    if binary is None:
        raise AtsError("pdftotext not found; install it with: brew install poppler")
    proc = subprocess.run([binary, pdf, "-"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AtsError("pdftotext failed: " + proc.stderr.strip())
    return proc.stdout


def check_pdf(pdf: str, cv: Dict[str, Any]) -> List[str]:
    return check_text(extract_text(pdf), cv)
