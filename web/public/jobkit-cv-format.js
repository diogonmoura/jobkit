/** Shared CV string formatting for HTML and DOCX. */

export function formatCvDates(s) {
  if (!s) return "";
  return String(s).replace(/\s-\s/g, " – ");
}

export function formatCvRole(title) {
  return String(title ?? "").toUpperCase();
}

export function classifyEntry(section, entry) {
  const key = section.title.toLowerCase();
  if (!key.includes("education")) return "job";

  const org = (entry.company || "").toLowerCase();
  if (/university|school|college|institut|academy/.test(org)) return "degree";

  const meta = (entry.meta || "").toLowerCase();
  if (/^independent\b/.test(meta) || /^independent\b/.test(org)) return "cert";
  if (entry.dates && /^(present|\d{4})$/i.test(entry.dates.trim())) return "cert";

  return "job";
}

export function partitionEntryBullets(bullets) {
  const plainLines = [];
  const listBullets = [];
  for (const b of bullets ?? []) {
    if (b.label && /^gpa$/i.test(b.label)) {
      plainLines.push(`${b.label}: ${b.text}`);
      continue;
    }
    if (!b.label && /^gpa\s*:/i.test(b.text)) {
      plainLines.push(b.text);
      continue;
    }
    listBullets.push(b);
  }
  return { plainLines, listBullets };
}

export function skillsParagraphText(bullets) {
  const text = (bullets ?? [])
    .map((b) => (b.label ? `${b.label}: ${b.text}` : b.text))
    .join("; ")
    .trim()
    .replace(/\.+$/, "");
  return text ? `${text}.` : "";
}
