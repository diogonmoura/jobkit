/**
 * One-page Word CV template (A4): steel blue-grey header bar, two-column borderless
 * table with right-aligned section labels, square bullets, thin blue-grey rules.
 * Works in the browser (bundled docx in /vendor) and in Node (pass the docx module) for previews.
 */
import { parseCvMarkdown } from "./jobkit-cv-parse.js";
import { formatCvDates } from "./jobkit-cv-format.js";

const ACCENT = "7F97B2";
const TEXT = "404040";
const HEADING = "262626";
const FONT = "Calibri";
const SIZE = 20; // half-points → 10pt

// A4 in twips and the template's proportions.
const PAGE_W = 11906;
const PAGE_H = 16838;
const MARGIN_L = 500;
const MARGIN_R = 560;
const CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R;
const TABLE_INDENT = 480;
const TABLE_W = CONTENT_W - TABLE_INDENT;
const LABEL_W = Math.round(TABLE_W * 0.18); // wide enough for "AI & TECH STACK" on one line
const BODY_W = TABLE_W - LABEL_W;

const cleanInline = (s) => String(s ?? "").replace(/\*\*/g, "").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").trim();

/** Keep at most maxWords, dropping whole trailing sentences (one-page template). */
export function clampWords(text, maxWords) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  if (t.split(" ").length <= maxWords) return t;
  let out = "";
  for (const sn of t.split(/(?<=[.!?])\s+/)) {
    if (out && (out + " " + sn).trim().split(/\s+/).length > maxWords) break;
    out = (out + " " + sn).trim();
  }
  return out.trim();
}

function splitSentences(text) {
  return cleanInline(text)
    .split(/(?<=[.!?])\s+(?=[A-Z])/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Turn the parsed markdown model into the four template sections. */
export function toTemplateModel(md, contactOverride = {}) {
  const model = parseCvMarkdown(md);
  const find = (re) => model.sections.filter((s) => re.test(s.title.toLowerCase()));
  const out = [];

  const exp = find(/experience/).flatMap((s) => s.entries);
  if (exp.length) {
    out.push({
      label: "Experience",
      entries: exp.map((e) => ({
        company: e.company || e.title,
        title: e.company ? e.title : "",
        meta: [formatCvDates(e.dates), e.location].filter(Boolean).join(" | "),
        lines: [],
        bullets: e.bullets.map((b) => ({ label: "", text: cleanInline(b.text) })),
      })),
    });
  }

  const projects = [];
  for (const s of find(/project/)) {
    for (const e of s.entries) {
      const pm = !e.company ? String(e.title || "").match(/^(.*?)\s*\(([^)]*)\)\s*$/) : null;
      const pparts = pm ? pm[2].split(",").map((x) => x.trim()) : [];
      projects.push({
        company: pm ? pm[1] : e.company || e.title,
        title: pm ? pparts[0] || "" : e.company ? e.title : "",
        meta: [
          ...pparts.slice(1).map((x) => x.charAt(0).toUpperCase() + x.slice(1)),
          e.location,
          formatCvDates(e.dates),
        ].filter(Boolean).join(" | "),
        lines: [],
        bullets: e.bullets.map((b) => ({ label: "", text: cleanInline(b.text) })),
      });
    }
    for (const b of s.bullets) {
      // "**Project name - Short description (Role, context, present):** text"
      const m = String(b.label || "").match(/^(.*?)\s*\(([^)]*)\)\s*$/);
      const name = m ? m[1] : b.label || "Project";
      const parts = m ? m[2].split(",").map((x) => x.trim()) : [];
      const role = parts[0] || "";
      const rest = parts.slice(1).map((x) => x.charAt(0).toUpperCase() + x.slice(1));
      projects.push({
        company: name,
        title: role,
        meta: rest.join(" | "),
        lines: [],
        bullets: splitSentences(b.text).map((t) => ({ label: "", text: t })),
      });
    }
  }
  if (projects.length) out.push({ label: "Projects", entries: projects });

  const edu = [];
  for (const s of find(/education/)) {
    for (const e of s.entries) {
      edu.push({
        company: e.company || e.title,
        title: e.company ? e.title : "",
        meta: [formatCvDates(e.dates), e.location].filter(Boolean).join(" | "),
        lines: e.bullets.map((b) => cleanInline(b.label ? `${b.label}: ${b.text}` : b.text)),
        bullets: [],
      });
    }
  }
  for (const s of find(/certification/)) {
    for (const b of s.bullets) {
      edu.push({
        company: b.label || "Certification",
        title: "",
        meta: "",
        lines: [],
        bullets: [{ label: "", text: cleanInline(b.label ? b.text : b.text) }],
      });
    }
    for (const e of s.entries) {
      edu.push({ company: e.company || e.title, title: e.company ? e.title : "", meta: formatCvDates(e.dates), lines: [], bullets: e.bullets.map((b) => ({ label: "", text: cleanInline(b.text) })) });
    }
  }
  if (edu.length) out.push({ label: "Education & Certifications", entries: edu });

  const skills = find(/skill|tech stack/).flatMap((s) => s.bullets);
  if (skills.length) {
    out.push({
      label: "AI & Tech stack",
      paragraph: skills.map((b) => ({ label: cleanInline(b.label), text: cleanInline(b.text) })),
    });
  }

  const prof = model.sections.find((s) => /profile|summary/.test(s.title.toLowerCase()));
  return {
    profile: prof ? clampWords(cleanInline(prof.profile || prof.paragraphs.join(" ")), 70) : "",
    name: contactOverride.name || model.name,
    email: contactOverride.email || model.contact.email,
    phone: contactOverride.phone || model.contact.phone,
    sections: out,
  };
}

export function buildCvDocument(md, docx, contactOverride = {}) {
  const { AlignmentType, BorderStyle, Document, Paragraph, ShadingType, Table, TableCell, TableLayoutType, TableRow, TextRun, VerticalAlign, WidthType } = docx;
  const t = toTemplateModel(md, contactOverride);

  const run = (text, o = {}) =>
    new TextRun({
      text,
      font: FONT,
      size: o.size ?? SIZE,
      bold: o.bold ?? false,
      italics: o.italics ?? false,
      color: o.color ?? TEXT,
      smallCaps: o.smallCaps ?? false,
      allCaps: o.allCaps ?? false,
    });
  const line = { line: 276, lineRule: "auto" };
  const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
  const rule = { style: BorderStyle.SINGLE, size: 6, color: ACCENT };

  // Header bar: name left, email centre, phone right (positions from the template).
  const header = new Paragraph({
    shading: { fill: ACCENT, type: ShadingType.CLEAR, color: "auto" },
    border: { top: { style: BorderStyle.SINGLE, size: 24, color: ACCENT }, bottom: { style: BorderStyle.SINGLE, size: 24, color: ACCENT }, left: { style: BorderStyle.SINGLE, size: 24, color: ACCENT, space: 4 }, right: { style: BorderStyle.SINGLE, size: 24, color: ACCENT, space: 4 } },
    spacing: { before: 0, after: 160, line: 400, lineRule: "auto" },
    tabStops: [
      { type: "left", position: Math.round(CONTENT_W * 0.395) },
      { type: "left", position: Math.round(CONTENT_W * 0.722) },
    ],
    children: [
      run(t.name.toUpperCase(), { size: 28, bold: true, color: "FFFFFF" }),
      run("\t" + (t.email || "").toUpperCase(), { size: 13, color: "FFFFFF" }),
      run("\t" + (t.phone || "").toUpperCase(), { size: 13, color: "FFFFFF" }),
    ],
  });

  const bullet = (b) =>
    new Paragraph({
      alignment: AlignmentType.BOTH,
      spacing: { before: 0, after: 50, ...line },
      indent: { left: 300, hanging: 170 },
      tabStops: [{ type: "left", position: 300 }],
      children: [
        run("▪\t", { size: 18, color: "595959" }),
        ...(b.label ? [run(`${b.label}: `, { bold: true })] : []),
        run(b.text),
      ],
    });

  const entryParas = (e) => {
    const paras = [
      new Paragraph({
        spacing: { before: 0, after: 60, ...line },
        children: [
          run(cleanInline(e.company).toUpperCase(), { bold: true, color: HEADING }),
          ...(e.title ? [run(" | ", { bold: true, color: HEADING }), run(cleanInline(e.title).toUpperCase(), { italics: true, color: HEADING })] : []),
        ],
      }),
    ];
    if (e.meta) paras.push(new Paragraph({ spacing: { before: 0, after: 10, ...line }, children: [run(e.meta)] }));
    for (const l of e.lines) paras.push(new Paragraph({ spacing: { before: 0, after: 10, ...line }, children: [run(l)] }));
    for (const b of e.bullets) paras.push(bullet(b));
    return paras;
  };

  const rows = [];
  if (t.profile) {
    rows.push(new TableRow({ cantSplit: true, children: [
      new TableCell({ width: { size: LABEL_W, type: WidthType.DXA }, borders: { top: none, left: none, right: none, bottom: rule }, margins: { top: 0, bottom: 110, left: 0, right: 170 }, children: [new Paragraph({ children: [] })] }),
      new TableCell({ width: { size: BODY_W, type: WidthType.DXA }, borders: { top: none, left: none, right: none, bottom: rule }, margins: { top: 0, bottom: 110, left: 60, right: 0 },
        children: [new Paragraph({ alignment: AlignmentType.BOTH, spacing: { before: 0, after: 0, ...line }, children: [run(t.profile)] })] }),
    ] }));
  }
  for (const s of t.sections) {
    const chunks = s.paragraph
      ? [[
          new Paragraph({
            alignment: AlignmentType.BOTH,
            spacing: { before: 0, after: 0, ...line },
            children: s.paragraph.flatMap((p, i) => [
              ...(i ? [run("; ")] : []),
              ...(p.label ? [run(`${p.label}: `, { bold: true })] : []),
              run(p.text.replace(/[.;]\s*$/, "")),
            ]),
          }),
        ]]
      : s.entries.map(entryParas);
    chunks.forEach((paras, idx) => {
      const first = idx === 0;
      const cells = [];
      if (first) {
        cells.push(
          new TableCell({
            rowSpan: chunks.length,
            width: { size: LABEL_W, type: WidthType.DXA },
            verticalAlign: VerticalAlign.TOP,
            borders: { top: none, left: none, right: none, bottom: rule },
            margins: { top: 100, bottom: 100, left: 0, right: 170 },
            children: [
              new Paragraph({
                alignment: AlignmentType.RIGHT,
                spacing: { before: 0, after: 0, line: 276, lineRule: "auto" },
                children: [run(s.label.toUpperCase(), { color: ACCENT, size: 19 })],
              }),
            ],
          }),
        );
      }
      cells.push(
        new TableCell({
          width: { size: BODY_W, type: WidthType.DXA },
          borders: { top: none, left: none, right: none, bottom: rule },
          margins: { top: 100, bottom: 110, left: 60, right: 0 },
          children: paras,
        }),
      );
      rows.push(new TableRow({ cantSplit: true, children: cells }));
    });
  }

  const table = new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    indent: { size: TABLE_INDENT, type: WidthType.DXA },
    layout: TableLayoutType.FIXED,
    columnWidths: [LABEL_W, BODY_W],
    borders: { top: none, bottom: none, left: none, right: none, insideHorizontal: none, insideVertical: none },
    rows,
  });

  return new Document({
    styles: { default: { document: { run: { font: FONT, size: SIZE, color: TEXT } } } },
    sections: [
      {
        properties: {
          page: {
            size: { width: PAGE_W, height: PAGE_H },
            margin: { top: 720, right: MARGIN_R, bottom: 400, left: MARGIN_L, header: 300, footer: 300 },
          },
        },
        children: [header, table],
      },
    ],
  });
}

let docxModule;
export async function downloadTemplateCvDocx(md, filename, contactOverride = {}) {
  if (!docxModule) docxModule = await import("/vendor/docx.bundle.mjs");
  const doc = buildCvDocument(md, docxModule, contactOverride);
  const blob = await docxModule.Packer.toBlob(doc);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
