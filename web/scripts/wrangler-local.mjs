// Run wrangler with private overrides: if wrangler.local.jsonc exists (gitignored), it is merged over
// wrangler.jsonc (objects deep-merged, "vars" merged key by key, arrays such as triggers.crons replaced) and
// wrangler runs against the merged file. Without it, this is plain `wrangler <args>`.
// Use it for your Worker name, routes/custom domain, cron, JOBKIT_LANG, AUTH_MODE, ADZUNA_COUNTRY, etc.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const base = path.join(root, "wrangler.jsonc");
const local = path.resolve(root, process.env.WRANGLER_LOCAL || "wrangler.local.jsonc");

/** JSONC → JSON: drop // and /* *\/ comments outside strings, and trailing commas. */
export function parseJsonc(text) {
  let out = "";
  for (let i = 0, inStr = false; i < text.length; i++) {
    const c = text[i];
    if (inStr) {
      out += c;
      if (c === "\\") out += text[++i] ?? "";
      else if (c === '"') inStr = false;
    } else if (c === '"') {
      inStr = true;
      out += c;
    } else if (c === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n") i++;
      out += "\n";
    } else if (c === "/" && text[i + 1] === "*") {
      i = text.indexOf("*/", i + 2) + 1 || text.length;
    } else out += c;
  }
  return JSON.parse(out.replace(/,(\s*[}\]])/g, "$1"));
}

export function merge(a, b) {
  if (Array.isArray(b) || typeof b !== "object" || b === null) return b;
  const out = { ...(typeof a === "object" && a && !Array.isArray(a) ? a : {}) };
  for (const [k, v] of Object.entries(b)) out[k] = merge(out[k], v);
  return out;
}

function main() {
  const args = process.argv.slice(2);
  let configArgs = [];
  if (fs.existsSync(local)) {
    const merged = merge(parseJsonc(fs.readFileSync(base, "utf8")), parseJsonc(fs.readFileSync(local, "utf8")));
    delete merged.$schema;
    const out = path.join(root, "wrangler.generated.json");
    fs.writeFileSync(out, JSON.stringify(merged, null, 2) + "\n");
    configArgs = ["--config", out];
    console.log(`wrangler-local: using ${path.basename(local)} (Worker "${merged.name}")`);
  }
  const bin = path.join(root, "node_modules", "wrangler", "bin", "wrangler.js");
  const r = spawnSync(process.execPath, [bin, ...args, ...configArgs], { stdio: "inherit", cwd: root });
  process.exit(r.status ?? 1);
}

if (process.argv[1] && path.resolve(process.argv[1]) === new URL(import.meta.url).pathname) main();
