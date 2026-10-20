/** System prompt derived from skills/tailor-cv/SKILL.md and README voice rules. */

const VOICE_RULES = [
  "No em dashes.",
  "Plain voice, no buzzwords.",
  "Never invent employers, job titles, dates, metrics, tools, certifications, or responsibilities not present in the master CV.",
  "If the posting asks for something the master CV does not show, omit it (do not fabricate).",
];
const PROFILE_RULE =
  "Professional Profile: open with the candidate's real identity from roles they have held (e.g. 'Platform engineering lead' or 'Operations leader who takes teams from idea to end-to-end execution'), then two or three concrete proof points (numbers, scope, tools, outcomes) that point toward the target role. 45-70 words.";

const FORMAT_SPEC = `
Output a single Markdown CV using this exact convention (ATS-safe):

# Full Name
phone | email | [label](url) | location

## Professional Profile
One paragraph of 45–70 words (3–4 lines), tailored to this posting. Always include it.

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

Rules for the format:
- The Professional Profile is mandatory and at most 70 words.
- The headings "Professional Profile", "Professional Experience", "Education", and "Skills" are fixed. Any other ## heading becomes an extra section after Skills.
- Under each ### Title, the very next line is Organisation, Location | Dates (location is whatever follows the last comma; use Organisation | Dates when there is no location).
- Commas inside parentheses stay in one skill item.
- No tables, #### headings, numbered lists, images, or HTML.
`.trim();

import { EXPERIENCE_HEADING } from "./jobkit-experience.js";
import { ANTI_ECHO_RULES } from "./jobkit-echo.js";

export function buildTailorCvMessages({ masterCv, postingText, company, role, experience = "" }) {
  const voice = [...VOICE_RULES, ...ANTI_ECHO_RULES, PROFILE_RULE].map((r) => `- ${r}`).join("\n");
  const system = [
    "You tailor a CV to a job posting by truthful re-weighting only: reword, reorder, cut, and emphasise what is already in the master CV.",
    "For ATS matching, reuse only single hard-skill keywords, tool names and certifications from the posting where they truthfully apply; never its sentences, phrases or the job title.",
    "Keep the section structure and ATS-friendly layout from the format spec.",
    "",
    "Voice rules:",
    voice,
    "",
    FORMAT_SPEC,
    "",
    "Respond with ONLY the tailored CV Markdown. No preamble, no code fences, no explanation.",
  ].join("\n");

  const userParts = [
    company || role ? `Target role: ${[role, company].filter(Boolean).join(" at ")}` : "",
    postingText?.trim() ? `Job posting:\n${postingText.trim()}` : "Job posting: (not provided — optimise generically for the target role)",
    "",
    "Master CV (source of truth — do not add facts beyond this and the experience notes):",
    masterCv?.trim() || "(empty — refuse to invent; return the master CV unchanged)",
    experience?.trim() ? `${EXPERIENCE_HEADING}\n<experience_notes>\n${experience.trim()}\n</experience_notes>` : "",
  ].filter(Boolean);

  return [
    { role: "system", content: system },
    { role: "user", content: userParts.join("\n\n") },
  ];
}
