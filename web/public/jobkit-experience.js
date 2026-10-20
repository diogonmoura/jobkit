/** Shared (browser + Worker): turn enabled experience files into one capped prompt block, newest first. */
export const EXPERIENCE_CHAR_LIMIT = 40000;

const when = (f) => String(f.version_date || f.uploaded_at || "").slice(0, 10);

export function sortExperienceNewestFirst(files) {
  return [...(files || [])].sort((a, b) => when(b).localeCompare(when(a)));
}

export function buildExperienceBlock(files, limit = EXPERIENCE_CHAR_LIMIT) {
  const on = sortExperienceNewestFirst(files).filter((f) => f && f.enabled !== false && String(f.content || "").trim());
  let text = "";
  let total = 0;
  for (const f of on) {
    const ver = [f.version_label, when(f)].filter(Boolean).join(", ");
    const part = `### Source: ${f.filename}${ver ? ` (version: ${ver})` : ""}\n${String(f.content).trim()}\n\n`;
    total += part.length;
    if (text.length < limit) text += part.slice(0, limit - text.length);
  }
  const truncated = total > limit;
  if (truncated) text += "\n[…truncated: the combined files exceed the length limit]\n";
  return { text: text.trim(), truncated, total, limit, count: on.length };
}

/** Default version date from a filename like "CV_2023-05.pdf" or "cv 2021.docx" (YYYY-MM-DD or ""). */
export function versionDateFromName(name) {
  const m = String(name || "").match(/(20\d{2}|19\d{2})(?:[-_. ]?(0[1-9]|1[0-2]))?/);
  return m ? `${m[1]}-${m[2] || "01"}-01` : "";
}

export const EXPERIENCE_HEADING = [
  "Detailed experience sources (the richest factual source; together with the candidate profile and master CV these are the ONLY facts you may use — never invent anything beyond them).",
  "They are ordered newest first. When sources conflict (titles, dates, numbers, current role), prefer the newer one. Use older CVs and notes for extra achievements and career narrative that the newer ones omit, as long as they do not contradict newer facts.",
].join(" ");
