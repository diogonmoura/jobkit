#!/usr/bin/env node
/**
 * Capture jobkit UI screenshots with demo data (no Supabase needed): ?demo=1 on the static files.
 * Run from web/: npm run screenshots
 */
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdir, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.join(__dirname, "..", "public");
const outDir = process.env.SHOTS_DIR ?? path.join(__dirname, "..", "screenshots");
const PORT = 9876;
const BASE = `http://127.0.0.1:${PORT}/index.html?demo=1`;

const MIME = {
  ".html": "text/html",
  ".css": "text/css",
  ".js": "text/javascript",
  ".md": "text/markdown",
  ".mjs": "text/javascript",
};

function startStaticServer() {
  return new Promise((resolve) => {
    const server = createServer(async (req, res) => {
      try {
        const url = new URL(req.url ?? "/", `http://127.0.0.1:${PORT}`);
        let filePath = path.join(publicDir, decodeURIComponent(url.pathname));
        if (url.pathname === "/") filePath = path.join(publicDir, "index.html");
        const body = await readFile(filePath);
        const ext = path.extname(filePath);
        res.writeHead(200, { "Content-Type": MIME[ext] ?? "application/octet-stream" });
        res.end(body);
      } catch {
        res.writeHead(404);
        res.end("Not found");
      }
    });
    server.listen(PORT, "127.0.0.1", () => resolve(server));
  });
}

async function setTheme(page, theme) {
  await page.evaluate((t) => {
    document.documentElement.dataset.theme = t;
    localStorage.setItem("hub-theme", t);
  }, theme);
}

async function shot(page, name, theme) {
  await setTheme(page, theme);
  await page.waitForTimeout(180);
  await page.screenshot({ path: path.join(outDir, name), fullPage: true });
}

async function gotoView(page, viewId) {
  await page.evaluate((id) => {
    document.getElementById("tab-" + id.replace("view-", ""))?.click();
  }, viewId);
  await page.waitForTimeout(200);
}

async function main() {
  await mkdir(outDir, { recursive: true });
  const server = await startStaticServer();
  const browser = await chromium.launch({ headless: true });
  try {
    const contexts = [
      { label: "desktop", viewport: { width: 1280, height: 800 } },
      { label: "mobile", viewport: { width: 390, height: 844 }, isMobile: true },
    ];

    for (const ctx of contexts) {
      const context = await browser.newContext({
        viewport: ctx.viewport,
        isMobile: ctx.isMobile ?? false,
      });
      const page = await context.newPage();
      page.setDefaultTimeout(20_000);
      await page.goto(BASE, { waitUntil: "networkidle" });
      await page.waitForSelector("#stats .stat", { timeout: 10_000 });

      await shot(page, `jobkit-applications-${ctx.label}-light.png`, "light");
      await shot(page, `jobkit-applications-${ctx.label}-dark.png`, "dark");

      await page.click("#tab-following");
      await page.waitForSelector("#watching", { timeout: 5_000 });
      await shot(page, `jobkit-following-${ctx.label}-light.png`, "light");
      await shot(page, `jobkit-following-${ctx.label}-dark.png`, "dark");

      await page.click("#tab-master-cv");
      await page.waitForSelector("#master-cv", { timeout: 5_000 });
      await shot(page, `jobkit-master-cv-${ctx.label}-light.png`, "light");
      await shot(page, `jobkit-master-cv-${ctx.label}-dark.png`, "dark");

      await page.click("#tab-applications");
      await page.waitForSelector("tr.row", { timeout: 5_000 });
      await page.click("tr.row");
      await page.waitForSelector("[data-generate]", { timeout: 10_000 });
      await shot(page, `jobkit-job-detail-${ctx.label}-light.png`, "light");
      await shot(page, `jobkit-job-detail-${ctx.label}-dark.png`, "dark");

      await page.click("[data-generate]");
      await page.waitForFunction(
        () => {
          const ta = document.querySelector("[id^='cv-md-']");
          return ta && ta.value.length > 200;
        },
        { timeout: 25_000 },
      );
      await page.waitForTimeout(300);
      await shot(page, `jobkit-generate-cv-${ctx.label}-light.png`, "light");
      await shot(page, `jobkit-generate-cv-${ctx.label}-dark.png`, "dark");

      await context.close();
    }

  } finally {
    await browser.close();
    server.close();
  }
  console.log("Screenshots written to", outDir);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
