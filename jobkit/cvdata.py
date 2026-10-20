"""Load cv.md (or a tailored copy) and validate it into the dict the
Typst template renders. Every problem is collected and reported at once,
each line starting with the field path, so a person can fix them in one go.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import cvmd

SECTIONS = ("contact", "profile", "experience", "education", "skills", "sections")


class CvError(ValueError):
    pass


def load_cv(path: str) -> Dict[str, Any]:
    if not path.endswith(".md"):
        raise CvError("{}: jobkit reads CVs written in Markdown (cv.md); see samples/cv.md for the "
                      "format".format(path))
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        doc = cvmd.parse(text)
    except cvmd.CvMdError as error:
        raise CvError("{}: {}".format(path, error)) from None
    return validate(doc)


class _Checker:
    def __init__(self) -> None:
        self.errors: List[str] = []

    def text(self, value: Any, path: str, required: bool = True) -> str:
        if value is None:
            result = ""
        elif isinstance(value, bool):
            result = "true" if value else "false"
        elif isinstance(value, (int, float, str)):
            result = str(value).strip()
        else:
            self.errors.append("{}: expected text".format(path))
            return ""
        if required and not result:
            self.errors.append("{}: missing".format(path))
        return result

    def items(self, value: Any, path: str, required: bool = True) -> List[Any]:
        if value is None:
            if required:
                self.errors.append("{}: missing".format(path))
            return []
        if not isinstance(value, list):
            self.errors.append("{}: expected a list".format(path))
            return []
        if required and not value:
            self.errors.append("{}: must not be empty".format(path))
        return value

    def mapping(self, value: Any, path: str) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            return value
        self.errors.append("{}: expected a mapping".format(path))
        return None

    def bullets(self, value: Any, path: str) -> List[Dict[str, str]]:
        out = []
        for j, bullet in enumerate(self.items(value, path + ".bullets", required=False)):
            p = "{}.bullets[{}]".format(path, j)
            if isinstance(bullet, dict):
                out.append({"label": self.text(bullet.get("label"), p + ".label", False),
                            "text": self.text(bullet.get("text"), p + ".text")})
            else:
                out.append({"label": "", "text": self.text(bullet, p)})
        return out


def validate(doc: Any) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        raise CvError("cv: expected a mapping with contact, profile, experience, education, skills")
    check = _Checker()

    contact = doc.get("contact")
    if contact is None:
        check.errors.append("contact: missing")
        contact = {}
    elif not isinstance(contact, dict):
        check.errors.append("contact: expected a mapping")
        contact = {}
    links = []
    for i, link in enumerate(check.items(contact.get("links"), "contact.links", required=False)):
        p = "contact.links[{}]".format(i)
        if check.mapping(link, p) is not None:
            links.append({"label": check.text(link.get("label"), p + ".label"),
                          "url": check.text(link.get("url"), p + ".url")})

    out: Dict[str, Any] = {
        "contact": {
            "name": check.text(contact.get("name"), "contact.name"),
            "phone": check.text(contact.get("phone"), "contact.phone", False),
            "email": check.text(contact.get("email"), "contact.email", False),
            "location": check.text(contact.get("location"), "contact.location", False),
            "links": links,
        },
        "profile": check.text(doc.get("profile"), "profile"),
        "experience": [],
        "education": [],
        "skills": [],
        "sections": [],
    }

    for i, job in enumerate(check.items(doc.get("experience"), "experience")):
        p = "experience[{}]".format(i)
        if check.mapping(job, p) is None:
            continue
        out["experience"].append({
            "title": check.text(job.get("title"), p + ".title"),
            "company": check.text(job.get("company"), p + ".company"),
            "location": check.text(job.get("location"), p + ".location", False),
            "dates": check.text(job.get("dates"), p + ".dates"),
            "bullets": check.bullets(job.get("bullets"), p),
        })

    for i, school in enumerate(check.items(doc.get("education"), "education")):
        p = "education[{}]".format(i)
        if check.mapping(school, p) is None:
            continue
        out["education"].append({
            "degree": check.text(school.get("degree"), p + ".degree"),
            "school": check.text(school.get("school"), p + ".school"),
            "location": check.text(school.get("location"), p + ".location", False),
            "dates": check.text(school.get("dates"), p + ".dates"),
            "bullets": check.bullets(school.get("bullets"), p),
        })

    skills = doc.get("skills")
    if not isinstance(skills, dict) or not skills:
        check.errors.append("skills: missing" if skills is None
                            else "skills: expected a mapping of category: [items]")
    else:
        for category, values in skills.items():
            p = "skills.{}".format(category)
            if not isinstance(values, list):
                values = [values]
            if not values:
                check.errors.append(p + ": must not be empty")
                continue
            out["skills"].append({"category": str(category),
                                  "items": [check.text(v, "{}[{}]".format(p, j)) for j, v in enumerate(values)]})

    for i, section in enumerate(check.items(doc.get("sections"), "sections", required=False)):
        p = "sections[{}]".format(i)
        if check.mapping(section, p) is None:
            continue
        values = check.items(section.get("items"), p + ".items")
        out["sections"].append({"title": check.text(section.get("title"), p + ".title"),
                                "items": [check.text(v, "{}.items[{}]".format(p, j)) for j, v in enumerate(values)]})

    for key in doc:
        if key not in SECTIONS:
            check.errors.append("{}: unknown section (allowed: {})".format(key, ", ".join(SECTIONS)))

    if check.errors:
        raise CvError("\n".join(check.errors))
    return out
