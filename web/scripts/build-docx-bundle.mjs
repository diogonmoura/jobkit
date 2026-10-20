#!/usr/bin/env node
import * as esbuild from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..");

await esbuild.build({
  entryPoints: [path.join(root, "scripts/docx-browser-entry.js")],
  bundle: true,
  format: "esm",
  platform: "browser",
  outfile: path.join(root, "public/vendor/docx.bundle.mjs"),
  logLevel: "info",
});

console.log("Wrote public/vendor/docx.bundle.mjs");
