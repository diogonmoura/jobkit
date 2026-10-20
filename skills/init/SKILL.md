---
name: init
description: Use when someone wants to start using jobkit in a folder, set up their CV data repo, or convert an existing CV (PDF, Word, LaTeX) into jobkit's cv.md.
---

# Set up a jobkit data repo

The data repo is the folder that holds this person's CV and job search data. It is usually a new, private git repo. Run everything from it.

1. Confirm the folder with the user. Do not scaffold inside the jobkit plugin itself or inside an unrelated project.
2. Find the engine: this skill's base directory is shown when the skill loads ("Base directory for this skill"). The engine is `<base directory>/../../jobkit/cli.py`. Use that absolute path.
3. Ask for the user's full name, then run `python3 "<engine>" init --name "<Full Name>"`. It creates `cv.md` (a sample), `jobkit.toml`, `applications.md`, `watchlist.md`, `CLAUDE.md`, `.gitignore`, `tailored/` and `Applications/`, keeps any file that already exists, and installs `~/.jobkit/launcher.py`. From now on every command is `python3 ~/.jobkit/launcher.py <command>`.
4. Run `python3 ~/.jobkit/launcher.py doctor`. For anything missing, give the user the install command it prints (for example `brew install typst poppler`); they run it.
5. Fill `cv.md`. If the user has an existing CV, read it (PDF with `pdftotext file.pdf -`, Word with the docx skill, LaTeX directly) and transcribe it into `cv.md` faithfully: same employers, titles, dates, bullets and numbers. Do not improve or add anything; this is the master the tailored versions are reworded from. Follow the format of the sample: `#` name, a contact line, the fixed `##` sections, `###` entries each followed by an `Organisation, Location | Dates` line, and `- **Label:** text` bullets.
6. Ask about writing rules (for example "no em dashes", British spelling) and put them in `[voice] rules` in `jobkit.toml`.
7. Render and check the master: `python3 ~/.jobkit/launcher.py render cv.md` then `python3 ~/.jobkit/launcher.py ats-check cv.pdf --source cv.md`. Show the user the PDF.
8. Optional, ask first: Telegram alerts (set `[notify] channel = "telegram"`; the bot token stays in the env file named there, never in the repo), and the daily board poll (`python3 ~/.jobkit/launcher.py schedule install`).
9. Suggest `git init` and a private remote for the folder if it is not a repo yet.
