/** Build a template-style .docx from cv.md (browser). */

import { parseCvMarkdown } from "./jobkit-cv-parse.js";
import {
  formatCvDates,
  formatCvRole,
  partitionEntryBullets,
  skillsParagraphText,
} from "./jobkit-cv-format.js";

const ACCENT = "7E97AD";
const TEXT = "595959";
const META = "777777";
const HEADING = "404040";
const HEADER_CONTACT = "E8EEF3";
const FONT = "Calibri";

let docxModule;

async function loadDocx() {
  if (!docxModule) docxModule = await import("/vendor/docx.bundle.mjs");
  return docxModule;
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function downloadCvDocx(md, filename = "CV.docx") {
  const {
    AlignmentType,
    BorderStyle,
    Document,
    Packer,
    Paragraph,
    ShadingType,
    Table,
    TableCell,
    TableLayoutType,
    TableRow,
    TextRun,
    VerticalAlign,
    WidthType,
  } = await loadDocx();

  const border = (color = ACCENT) => ({
    top: { style: BorderStyle.SINGLE, size: 4, color },
    bottom: { style: BorderStyle.SINGLE, size: 4, color },
    left: { style: BorderStyle.SINGLE, size: 4, color: "FFFFFF" },
    right: { style: BorderStyle.SINGLE, size: 4, color: "FFFFFF" },
  });

  const bodyLine = { line: 348, lineRule: "auto" };
  const tight = { after: 60, ...bodyLine };
  const bulletAfter = { after: 40, ...bodyLine };
  const metaAfter = { after: 90, ...bodyLine };
  const sectionEdge = 120;
  const sectionEndEdge = 140;
  const sectionInner = 40;

  const run = (text, opts = {}) =>
    new TextRun({
      text,
      font: FONT,
      size: opts.size ?? 20,
      bold: opts.bold ?? false,
      italics: opts.italics ?? false,
      color: opts.color ?? TEXT,
    });

  const model = parseCvMarkdown(md);

  const contactParts = [model.contact.email, model.contact.phone].filter(Boolean).map((s) =>
    s.toUpperCase(),
  );

  const headerChildren = [
    run(model.name.toUpperCase(), { size: 28, bold: false, color: "FFFFFF" }),
  ];
  if (contactParts.length) {
    headerChildren.push(run("\t", { size: 20, color: "FFFFFF" }));
    contactParts.forEach((part, i) => {
      if (i > 0) headerChildren.push(run("\t", { size: 20, color: HEADER_CONTACT }));
      headerChildren.push(run(part, { size: 14, bold: false, color: HEADER_CONTACT }));
    });
  }

  const header = new Paragraph({
    spacing: { after: 100, ...bodyLine },
    shading: { fill: ACCENT, type: ShadingType.CLEAR, color: "auto" },
    border: border(ACCENT),
    children: headerChildren,
  });

  const entryParagraphs = (entry) => {
    const paras = [];

    if (entry.kind === "cert") {
      paras.push(
        new Paragraph({
          alignment: AlignmentType.BOTH,
          spacing: tight,
          children: [run(entry.title.toUpperCase(), { bold: true, color: HEADING, size: 22 })],
        }),
      );
    } else {
      const company = (entry.company || entry.title).toUpperCase();
      const role = entry.company ? formatCvRole(entry.title) : "";
      paras.push(
        new Paragraph({
          alignment: AlignmentType.BOTH,
          spacing: { after: 20, line: 322, lineRule: "auto" },
          children: [
            run(`${company} | `, { bold: true, color: HEADING, size: 22 }),
            run(role, { bold: false, italics: true, color: HEADING, size: 22 }),
          ],
        }),
      );
      paras.push(
        new Paragraph({
          alignment: AlignmentType.BOTH,
          spacing: metaAfter,
          children: [
            run(
              [formatCvDates(entry.dates), entry.location].filter(Boolean).join(" | "),
              { bold: false, size: 20, color: META },
            ),
          ],
        }),
      );
    }

    const { plainLines, listBullets } = partitionEntryBullets(entry.bullets);
    for (const line of plainLines) {
      paras.push(
        new Paragraph({
          alignment: AlignmentType.BOTH,
          spacing: tight,
          children: [run(line, { bold: false, size: 20 })],
        }),
      );
    }

    for (const b of listBullets) {
      paras.push(
        new Paragraph({
          alignment: AlignmentType.BOTH,
          spacing: bulletAfter,
          bullet: { level: 0 },
          children: b.label
            ? [run(`${b.label}: `, { bold: true, size: 20 }), run(b.text, { bold: false, size: 20 })]
            : [run(b.text, { bold: false, size: 20 })],
        }),
      );
    }
    return paras;
  };

  const sectionBody = (section) => {
    const isSkills =
      section.sidebar.toLowerCase().includes("tech stack") ||
      section.title.toLowerCase() === "skills";
    if (isSkills && section.bullets.length) {
      const text = skillsParagraphText(section.bullets);
      return [
        new Paragraph({
          alignment: AlignmentType.BOTH,
          spacing: tight,
          children: [run(text, { bold: false, size: 20 })],
        }),
      ];
    }
    if (section.entries.length) return section.entries.flatMap(entryParagraphs);
    const paras = [];
    for (const b of section.bullets) {
      paras.push(
        new Paragraph({
          alignment: AlignmentType.BOTH,
          spacing: bulletAfter,
          bullet: { level: 0 },
          children: b.label
            ? [run(`${b.label}: `, { bold: true, size: 20 }), run(b.text, { bold: false, size: 20 })]
            : [run(b.text, { bold: false, size: 20 })],
        }),
      );
    }
    return paras;
  };

  const rows = [];
  const sections = model.sections.filter((s) => s.title.toLowerCase() !== "professional profile");

  for (const section of sections) {
    const chunks = section.entries.length
      ? section.entries.map((e) => entryParagraphs(e))
      : [sectionBody(section)];
    const count = chunks.length;
    chunks.forEach((paras, idx) => {
      const isStart = idx === 0;
      const isEnd = idx === count - 1;
      const labelMargins = {
        top: isStart ? sectionEdge : sectionInner,
        bottom: isEnd ? sectionEndEdge : sectionInner,
        left: 0,
        right: 100,
      };
      const bodyMargins = {
        top: isStart ? sectionEdge : sectionInner,
        bottom: isEnd ? sectionEndEdge : sectionInner,
        left: 80,
        right: 0,
      };
      rows.push(
        new TableRow({
          children: [
            ...(idx === 0
              ? [
                  new TableCell({
                    rowSpan: count,
                    verticalAlign: VerticalAlign.TOP,
                    borders: border(),
                    margins: labelMargins,
                    children: [
                      new Paragraph({
                        alignment: AlignmentType.RIGHT,
                        spacing: { after: 0, line: 276, lineRule: "auto" },
                        children: [
                          run(section.sidebar.toUpperCase(), { size: 21, color: ACCENT, bold: false }),
                        ],
                      }),
                    ],
                  }),
                  new TableCell({
                    rowSpan: count,
                    borders: border(),
                    children: [new Paragraph("")],
                  }),
                ]
              : []),
            new TableCell({
              borders: border(),
              margins: bodyMargins,
              children: paras,
            }),
          ],
        }),
      );
    });
  }

  const table = new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    columnWidths: [1500, 100, 7800],
    rows,
  });

  const doc = new Document({
    sections: [
      {
        properties: {
          page: { margin: { top: 745, right: 616, bottom: 200, left: 491 } },
        },
        children: [header, table],
      },
    ],
  });

  const blob = await Packer.toBlob(doc);
  triggerDownload(blob, filename);
}
