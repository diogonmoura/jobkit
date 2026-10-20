---
name: tailor-cv
description: Use when the user wants their CV adapted to a specific job posting (a URL or pasted text). Writes a tailored CV from cv.md, renders and ATS-checks the PDF, files it under Applications/, and logs the application in the tracker.
---

# Tailor the CV to a job posting

Run every engine command as `python3 ~/.jobkit/launcher.py <command>` from the data repo (the folder with `jobkit.toml`). If the launcher is missing, run `/jobkit:init` first.

## Rules

- Truthful re-weighting only. Reword, reorder, cut and emphasise what is already in `cv.md`. Never add an accomplishment, number, tool, employer, certification or responsibility that is not there. If the posting asks for something the CV does not show, leave it out and tell the user about the gap.
- Follow every rule in `[voice] rules` in `jobkit.toml`, and any personal rules in the data repo's `CLAUDE.md`.
- Use the posting's own words for hard skills, tools, certifications and the job title wherever they truthfully apply. ATS engines (Workday, Greenhouse, iCIMS, Taleo, Lever) match keywords verbatim: "interdepartmental teamwork" does not match "cross-functional collaboration".
- Keep the section structure. The template fixes the headings to the names ATS parsers look for and lays every job out as title, then "Company, Location | Dates".

## CV format (Markdown, one fixed convention)

```markdown
# Full Name
phone | email | [label](url) | location

## Professional Profile
One paragraph. It may wrap over several lines.

## Professional Experience

### Job Title
Company, Location | Dates
- **Label:** text
- text without a label

## Education

### Degree
School, Location | Dates

## Skills
- **Category:** item, item, item

## Languages
- item
```

- The four headings above are fixed (ATS parsers look for them); any other `##` heading becomes an extra section after Skills.
- Under each `### Title`, the very next line is `Organisation, Location | Dates`. The location is whatever follows the last comma; leave it out with `Organisation | Dates`.
- Commas inside parentheses stay in one skill: `Low-Code Automation (Power Automate, n8n)`.
- No tables, `####` headings, numbered lists, images or HTML: the engine rejects them with a line number, because the PDF could not show them ATS-safely.

## Steps

1. Read the posting. Fetch the URL; if that fails, ask the user to paste it. Note the company, the exact role title, and the verbatim hard-skill keywords.
2. Read `cv.md` and `jobkit.toml`.
3. Pick a slug: the company in lowercase letters and digits (`acme`); if `tailored/<slug>.md` exists for another role, add a role suffix (`acme_data`).
4. Write `tailored/<slug>.md` following the same format as `cv.md`: adjust the profile, the order and wording of bullets, and the skills to fit the posting.
5. `python3 ~/.jobkit/launcher.py render tailored/<slug>.md` writes `tailored/<slug>.pdf`. Fix the file and re-render on any error.
6. `python3 ~/.jobkit/launcher.py ats-check tailored/<slug>.pdf --source tailored/<slug>.md`. If it reports problems, fix the source and re-render. Never file a PDF that fails.
7. Read `pdftotext tailored/<slug>.pdf -` yourself once and confirm it reads as clean single-column text, and check the page count is what the user expects (usually 1 to 2).
8. `python3 ~/.jobkit/launcher.py file-cv --company "<Company>" --pdf tailored/<slug>.pdf` copies it to `Applications/<today> - <Company>/`.
9. Only when the user confirms they applied (or asks you to log it): `python3 ~/.jobkit/launcher.py add --company "<Company>" --role "<Role>" --cv <slug> --posting "<url>"`. Read the last line. If it says the company is not followed because its board has no connector, run `/jobkit:build-connector` with the posting URL.
10. If `[tracker] artifact_url` is set in `jobkit.toml`, run `/jobkit:sync-tracker`.
11. Tell the user: the PDF path, the posting keywords you worked in, and any requirement the CV does not cover.
