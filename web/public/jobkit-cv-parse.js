/** Parse jobkit cv.md into a model for the Word CV template. */

import { classifyEntry, formatCvDates } from "./jobkit-cv-format.js";

const SIDEBAR_LABELS = {
  "professional experience": "Experience",
  experience: "Experience",
  projects: "Projects",
  education: "Education & Certifications",
  "education & certifications": "Education & Certifications",
  skills: "AI & Tech stack",
  "ai & tech stack": "AI & Tech stack",
  languages: "Languages",
};

export function sidebarLabel(sectionTitle) {
  const key = sectionTitle.trim().toLowerCase();
  if (SIDEBAR_LABELS[key]) return SIDEBAR_LABELS[key];
  return sectionTitle.trim();
}

function parseContactLine(line) {
  const parts = line.split("|").map((p) => p.trim());
  const out = { email: "", phone: "", location: "", links: [], raw: line };
  for (const part of parts) {
    const email = part.match(/[\w.+-]+@[\w.-]+\.\w+/);
    if (email) {
      out.email = email[0];
      continue;
    }
    const phone = part.match(/(\+\d[\d\s().-]{6,}|\(\+\d+\)[\d\s-]+)/);
    if (phone) {
      out.phone = phone[0].trim();
      continue;
    }
    const link = part.match(/\[([^\]]+)\]\(([^)]+)\)/);
    if (link) {
      out.links.push({ label: link[1], url: link[2] });
      continue;
    }
    if (part && !out.location) out.location = part;
  }
  return out;
}

function parseMetaLine(meta) {
  const pipe = meta.indexOf("|");
  const orgPart = (pipe >= 0 ? meta.slice(0, pipe) : meta).trim();
  const datesPart = pipe >= 0 ? meta.slice(pipe + 1).trim() : "";
  const commaIdx = orgPart.indexOf(",");
  let company = orgPart;
  let location = "";
  if (commaIdx > 0) {
    company = orgPart.slice(0, commaIdx).trim();
    location = orgPart.slice(commaIdx + 1).trim();
  }
  return { company, location, dates: datesPart };
}

function parseBullet(line) {
  const raw = line.replace(/^[-*]\s+/, "");
  const m = raw.match(/^\*\*([^*]+):\*\*\s*(.*)$/);
  if (m) return { label: m[1].trim(), text: m[2].trim() };
  return { label: "", text: raw.trim() };
}

/**
 * @returns {{ name: string, contact: ReturnType<typeof parseContactLine>, sections: object[], error?: string }}
 */
export function parseCvMarkdown(md) {
  if (!md?.trim()) return { name: "", contact: parseContactLine(""), sections: [] };

  const lines = md.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  if (!lines[i]?.startsWith("# ")) {
    return { name: "", contact: parseContactLine(""), sections: [], error: "CV should start with # Full Name" };
  }

  const name = lines[i].slice(2).trim();
  i++;
  while (i < lines.length && !lines[i].trim()) i++;
  const contact = parseContactLine(
    i < lines.length && !lines[i].startsWith("#") ? lines[i].trim() : "",
  );
  if (contact.raw) i++;

  const sections = [];

  while (i < lines.length) {
    while (i < lines.length && !lines[i].trim()) i++;
    if (i >= lines.length) break;
    if (!lines[i].startsWith("## ")) {
      i++;
      continue;
    }

    const title = lines[i].slice(3).trim();
    i++;
    const section = {
      title,
      sidebar: sidebarLabel(title),
      profile: "",
      entries: [],
      bullets: [],
      paragraphs: [],
    };

    const key = title.toLowerCase();
    if (key === "professional profile") {
      const paras = [];
      while (i < lines.length && lines[i].trim() && !lines[i].startsWith("#")) {
        paras.push(lines[i].trim());
        i++;
      }
      section.profile = paras.join(" ");
      sections.push(section);
      continue;
    }

    while (i < lines.length && !lines[i].startsWith("## ")) {
      while (i < lines.length && !lines[i].trim()) i++;
      if (i >= lines.length || lines[i].startsWith("## ")) break;

      if (lines[i].startsWith("### ")) {
        const jobTitle = lines[i].slice(4).trim();
        i++;
        while (i < lines.length && !lines[i].trim()) i++;
        const meta = i < lines.length && !lines[i].startsWith("#") && !lines[i].trim().startsWith("-")
          ? lines[i].trim()
          : "";
        if (meta && !meta.startsWith("-")) i++;
        const { company, location, dates } = parseMetaLine(meta);
        const bullets = [];
        while (i < lines.length && lines[i].trim().startsWith("-")) {
          bullets.push(parseBullet(lines[i]));
          i++;
        }
        const entry = {
          title: jobTitle,
          company,
          location,
          dates: formatCvDates(dates),
          bullets,
          meta,
        };
        entry.kind = classifyEntry(section, entry);
        section.entries.push(entry);
        continue;
      }

      if (lines[i].trim().startsWith("-")) {
        while (i < lines.length && lines[i].trim().startsWith("-")) {
          section.bullets.push(parseBullet(lines[i]));
          i++;
        }
        continue;
      }

      if (lines[i].trim()) {
        section.paragraphs.push(lines[i].trim());
      }
      i++;
    }

    sections.push(section);
  }

  return { name, contact, sections };
}
