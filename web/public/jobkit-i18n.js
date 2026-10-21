/**
 * UI language. English is the source language of the markup; other locales live in /locales/<lang>.js and are
 * applied by translating text nodes and a few attributes as they appear (also strings set later by scripts,
 * toasts, confirm/prompt dialogs). The language comes from JOBKIT_LANG (injected by the Worker as
 * window.__JOBKIT_LANG); "en" does nothing.
 */
const LANG = String(globalThis.__JOBKIT_LANG || "en");
const SUPPORTED = ["pt-PT"];
const ATTRS = ["placeholder", "title", "aria-label"];

let strings = {};
let patterns = [];
let prefixes = [];

export function t(s) {
  if (!s || typeof s !== "string") return s;
  const lead = s.match(/^\s*/)[0];
  const trail = s.match(/\s*$/)[0];
  const core = s.trim();
  if (!core) return s;
  if (strings[core] != null) return lead + strings[core] + trail;
  for (const [re, rep] of patterns) if (re.test(core)) return lead + core.replace(re, rep) + trail;
  // "Error: <details>" style messages: translate the known prefix, keep the rest.
  for (const p of prefixes) if (core.startsWith(p + " ")) return lead + strings[p] + core.slice(p.length) + trail;
  return s;
}

function translateNode(node) {
  if (node.nodeType === 3) {
    const p = node.parentNode;
    if (!p || p.closest?.("textarea,script,style,pre,code,[data-no-i18n],.cv-preview-pane,.cv-document-wrap,.app-answers dd")) return;
    const next = t(node.nodeValue);
    if (next !== node.nodeValue) node.nodeValue = next;
    return;
  }
  if (node.nodeType !== 1) return;
  if (node.matches("script,style,textarea,[data-no-i18n],.cv-preview-pane,.cv-document-wrap,.app-answers dd")) return;
  for (const a of ATTRS) {
    const v = node.getAttribute(a);
    if (v) {
      const next = t(v);
      if (next !== v) node.setAttribute(a, next);
    }
  }
  for (const c of node.childNodes) translateNode(c);
}

async function start() {
  if (LANG === "en" || !SUPPORTED.includes(LANG)) return;
  const mod = await import(`/locales/${LANG}.js`);
  strings = mod.default.strings || {};
  patterns = (mod.default.patterns || []).map(([re, rep]) => [new RegExp(re), rep]);
  prefixes = Object.keys(strings)
    .filter((k) => k.endsWith(":"))
    .sort((a, b) => b.length - a.length);
  document.documentElement.lang = LANG;
  document.title = t(document.title);
  const run = () => translateNode(document.body);
  if (document.body) run();
  else document.addEventListener("DOMContentLoaded", run, { once: true });
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "characterData") translateNode(r.target);
      else if (r.type === "attributes") translateNode(r.target);
      else for (const n of r.addedNodes) translateNode(n);
    }
  }).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  const { confirm, prompt, alert } = window;
  window.confirm = (m) => confirm.call(window, t(m));
  window.prompt = (m, d) => prompt.call(window, t(m), d);
  window.alert = (m) => alert.call(window, t(m));
}

export const ready = start();
