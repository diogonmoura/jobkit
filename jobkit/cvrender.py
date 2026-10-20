"""Render a validated CV to PDF with the Typst template.

The data goes to Typst as a JSON file next to a copy of the template in a
temporary directory, so the template reads it with a relative path and no
--root tricks. System fonts are ignored so a CV renders the same on every
machine with the font Typst embeds (Libertinus Serif).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional

import cvdata

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(PLUGIN_ROOT, "templates", "cv.typ")


class RenderError(RuntimeError):
    pass


def render(cv: Dict[str, Any], out_pdf: str, typst: str = "typst", template: str = TEMPLATE) -> str:
    binary = shutil.which(typst)
    if binary is None:
        raise RenderError("typst not found; install it with: brew install typst")
    out_pdf = os.path.abspath(out_pdf)
    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        shutil.copy(template, os.path.join(work, "cv.typ"))
        with open(os.path.join(work, "data.json"), "w", encoding="utf-8") as handle:
            json.dump(cv, handle, ensure_ascii=False)
        proc = subprocess.run(
            [binary, "compile", "--ignore-system-fonts", "--input", "data=data.json", "cv.typ", out_pdf],
            cwd=work, capture_output=True, text=True,
        )
    if proc.returncode != 0:
        raise RenderError("typst failed:\n" + proc.stderr.strip())
    return out_pdf


def render_file(cv_path: str, out_pdf: Optional[str] = None) -> str:
    cv = cvdata.load_cv(cv_path)
    return render(cv, out_pdf or os.path.splitext(cv_path)[0] + ".pdf")
