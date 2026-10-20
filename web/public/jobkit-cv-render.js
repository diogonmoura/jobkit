/** Render jobkit cv.md to HTML matching the Word CV template (jobkit-cv-template-docx.js). */

import { parseCvMarkdown } from "./jobkit-cv-parse.js";
import { toTemplateModel } from "./jobkit-cv-template-docx.js";
import {
  formatCvDates,
  formatCvRole,
  partitionEntryBullets,
  skillsParagraphText,
} from "./jobkit-cv-format.js";

const esc = (s) =>
  String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

function inlineMd(text) {
  let s = esc(text);
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  return s;
}

function contactHeaderHtml(name, contact) {
  const chips = [];
  if (contact.email) {
    chips.push(`<span class="cv-contact-item">${esc(contact.email.toUpperCase())}</span>`);
  }
  if (contact.phone) {
    chips.push(`<span class="cv-contact-item">${esc(contact.phone.toUpperCase())}</span>`);
  }
  for (const l of contact.links) {
    chips.push(
      `<span class="cv-contact-item"><a href="${esc(l.url)}">${esc(l.label.toUpperCase())}</a></span>`,
    );
  }
  return `<header class="cv-word-header">
    <div class="cv-word-header__name">${esc(name)}</div>
    ${chips.join("")}
  </header>`;
}

function entryHeadline(entry) {
  if (entry.kind === "cert") {
    return `<p class="cv-entry-heading-only">${esc(entry.title.toUpperCase())}</p>`;
  }
  const company = esc((entry.company || entry.title).toUpperCase());
  const role = esc(formatCvRole(entry.company ? entry.title : ""));
  return `<p class="cv-entry-head"><span class="cv-entry-company">${company}</span><span class="cv-entry-sep"> | </span><span class="cv-entry-role">${role}</span></p>`;
}

function entryMeta(entry) {
  if (entry.kind === "cert") return "";
  const dates = formatCvDates(entry.dates);
  const bits = [dates, entry.location].filter(Boolean);
  if (!bits.length && entry.meta) return `<p class="cv-entry-meta">${inlineMd(formatCvDates(entry.meta))}</p>`;
  return `<p class="cv-entry-meta">${esc(bits.join(" | "))}</p>`;
}

function bulletsHtml(bullets, { round = false } = {}) {
  if (!bullets?.length) return "";
  const cls = round ? "cv-word-bullets cv-word-bullets--round" : "cv-word-bullets";
  return `<ul class="${cls}">${bullets
    .map((b) => {
      if (b.label) {
        return `<li><span class="cv-bullet-label">${esc(b.label)}:</span> <span class="cv-bullet-text">${inlineMd(b.text)}</span></li>`;
      }
      return `<li><span class="cv-bullet-text">${inlineMd(b.text)}</span></li>`;
    })
    .join("")}</ul>`;
}

function entryBlock(entry) {
  const { plainLines, listBullets } = partitionEntryBullets(entry.bullets);
  const round = entry.kind === "cert";
  const plain = plainLines.map((l) => `<p class="cv-body-text cv-plain-line">${inlineMd(l)}</p>`).join("");
  return `<div class="cv-entry">${entryHeadline(entry)}${entryMeta(entry)}${plain}${bulletsHtml(listBullets, { round })}</div>`;
}

function bodyBlock(section) {
  const chunks = [];
  if (section.profile) {
    chunks.push(`<p class="cv-body-text">${inlineMd(section.profile)}</p>`);
  }
  for (const entry of section.entries) {
    chunks.push(entryBlock(entry));
  }
  if (section.bullets.length && !section.entries.length) {
    chunks.push(bulletsHtml(section.bullets));
  }
  if (section.paragraphs.length) {
    for (const p of section.paragraphs) {
      chunks.push(`<p class="cv-body-text">${inlineMd(p)}</p>`);
    }
  }
  if (section.bullets.length && section.entries.length) {
    chunks.push(bulletsHtml(section.bullets));
  }
  return chunks.join("");
}

function skillsAsParagraph(section) {
  if (!section.bullets.length) return bodyBlock(section);
  const text = skillsParagraphText(section.bullets);
  return `<p class="cv-body-text cv-skills-line">${inlineMd(text)}</p>`;
}

function tableSectionRows(section) {
  const isSkills =
    section.sidebar.toLowerCase().includes("tech stack") ||
    section.title.toLowerCase() === "skills";

  const bodyParts = [];
  if (isSkills && section.bullets.length && !section.entries.length) {
    bodyParts.push(skillsAsParagraph(section));
  } else if (section.entries.length) {
    for (const entry of section.entries) {
      bodyParts.push(entryBlock(entry));
    }
    if (section.bullets.length) bodyParts.push(bulletsHtml(section.bullets));
  } else {
    const block = bodyBlock(section);
    if (block.trim()) bodyParts.push(block);
  }

  if (!bodyParts.length) return "";

  const label = esc(section.sidebar.toUpperCase());
  const n = bodyParts.length;
  return bodyParts
    .map(
      (html, idx) => `<tr class="cv-word-row${idx === 0 ? " cv-word-row--section-start" : ""}${idx === n - 1 ? " cv-word-row--section-end" : ""}${idx < n - 1 ? " cv-word-row--rule" : ""}">
      ${idx === 0 ? `<td class="cv-word-label" rowspan="${n}"><span>${label}</span></td><td class="cv-word-gap" rowspan="${n}"></td>` : ""}
      <td class="cv-word-body">${html}</td>
    </tr>`,
    )
    .join("");
}

function templateEntryHtml(e) {
  const role = e.title ? `<span class="cv-entry-sep"> | </span><span class="cv-entry-role">${esc(formatCvRole(e.title))}</span>` : "";
  const head = `<p class="cv-entry-head"><span class="cv-entry-company">${esc(String(e.company || "").toUpperCase())}</span>${role}</p>`;
  const meta = e.meta ? `<p class="cv-entry-meta">${esc(e.meta)}</p>` : "";
  const lines = (e.lines || []).map((l) => `<p class="cv-body-text cv-plain-line">${inlineMd(l)}</p>`).join("");
  return `<div class="cv-entry">${head}${meta}${lines}${bulletsHtml(e.bullets)}</div>`;
}

function templateSectionRows(sec) {
  let parts;
  if (sec.paragraph) {
    const text = sec.paragraph.map((p) => (p.label ? `<span class="cv-bullet-label">${esc(p.label)}:</span> ${inlineMd(p.text.replace(/[.;]\s*$/, ""))}` : inlineMd(p.text.replace(/[.;]\s*$/, "")))).join("; ");
    parts = [`<p class="cv-body-text cv-skills-line">${text}</p>`];
  } else {
    parts = sec.entries.map(templateEntryHtml);
  }
  if (!parts.length) return "";
  const n = parts.length;
  return parts
    .map((html, idx) => `<tr class="cv-word-row${idx === 0 ? " cv-word-row--section-start" : ""}${idx === n - 1 ? " cv-word-row--section-end" : ""}">
      ${idx === 0 ? `<td class="cv-word-label" rowspan="${n}"><span>${esc(sec.label.toUpperCase())}</span></td><td class="cv-word-gap" rowspan="${n}"></td>` : ""}
      <td class="cv-word-body">${html}</td></tr>`)
    .join("");
}

/**
 * Same structure as the Word export: Profile, Experience, Projects, Education & Certifications, AI & Tech stack.
 * @returns {{ html: string, model?: object, error?: string }}
 */
export function cvMarkdownToHtml(md) {
  const model = parseCvMarkdown(md);
  if (model.error) {
    return { html: `<pre class="cv-raw">${esc(md)}</pre>`, error: model.error, model };
  }
  const tpl = toTemplateModel(md);
  let tableRows = "";
  if (tpl.profile) {
    tableRows += `<tr class="cv-word-row cv-word-row--profile"><td class="cv-word-label"></td><td class="cv-word-gap"></td><td class="cv-word-body"><p class="cv-body-text">${inlineMd(tpl.profile)}</p></td></tr>`;
  }
  for (const sec of tpl.sections) tableRows += templateSectionRows(sec);
  const contact = { ...model.contact, email: tpl.email, phone: tpl.phone, links: [] };
  const html = `<article class="cv-document cv-word">
    ${contactHeaderHtml(tpl.name || model.name, contact)}
    <table class="cv-word-table" role="presentation">
      <colgroup><col class="cv-col-label" /><col class="cv-col-gap" /><col class="cv-col-body" /></colgroup>
      <tbody>${tableRows}</tbody>
    </table>
  </article>`;
  return { html, model };
}

export { parseCvMarkdown };
