/** Client-side text extraction for experience uploads (no AI, no upload of the binary). */
let pdfjs;
async function pdfText(buf) {
  if (!pdfjs) {
    pdfjs = await import("/vendor/pdf.min.mjs");
    pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.min.mjs";
  }
  const doc = await pdfjs.getDocument({ data: new Uint8Array(buf) }).promise;
  const pages = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const tc = await (await doc.getPage(i)).getTextContent();
    let line = "", out = [];
    for (const it of tc.items) {
      line += it.str;
      if (it.hasEOL) { out.push(line); line = ""; } else if (it.str && !/\s$/.test(it.str)) line += " ";
    }
    if (line.trim()) out.push(line);
    pages.push(out.map((l) => l.replace(/\s+/g, " ").trim()).join("\n"));
  }
  return pages.join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
}

function loadMammoth() {
  if (window.mammoth) return Promise.resolve(window.mammoth);
  return new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "/vendor/mammoth.browser.min.js";
    s.onload = () => res(window.mammoth);
    s.onerror = () => rej(new Error("mammoth"));
    document.head.appendChild(s);
  });
}

export function fileKind(f) {
  const n = f.name.toLowerCase();
  if (n.endsWith(".pdf") || f.type === "application/pdf") return "pdf";
  if (n.endsWith(".docx")) return "docx";
  if (/\.(md|markdown|txt)$/.test(n) || /^text\//.test(f.type)) return "md";
  return "";
}

export async function extractText(f) {
  const kind = fileKind(f);
  if (kind === "md") return { kind, text: await f.text() };
  const buf = await f.arrayBuffer();
  if (kind === "pdf") return { kind, text: await pdfText(buf) };
  if (kind === "docx") {
    const m = await loadMammoth();
    const r = await m.extractRawText({ arrayBuffer: buf });
    return { kind, text: String(r.value || "").replace(/\n{3,}/g, "\n\n").trim() };
  }
  throw new Error("Unsupported format: " + f.name);
}
