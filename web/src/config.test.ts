import { test } from "node:test";
import { localizeFlag } from "../public/jobkit-flags.js";
import { companyDomain, domainMap } from "../public/jobkit-logo.js";
import assert from "node:assert/strict";
import { standardFlags, rubricText } from "./scan/pipeline";
import { normalizeProfile } from "./preferences";
import { tr, langName } from "./i18n";
import type { Env } from "./types";
// @ts-ignore plain JS helper
import { parseJsonc, merge } from "../scripts/wrangler-local.mjs";

const profile = normalizeProfile({
  roles: ["Chief of Staff"],
  home_base: { city: "Porto", country: "Portugal", region: "Europe" },
  custom_flags: ["Check the travel load", { label: "Strategic or admin role?", title: "chief of staff" }],
  rubric_notes: "Payments focus beats generic ops.",
});
const opening = { externalKey: "k", company: "Acme", title: "Chief of Staff", url: "", location: "Remote, US", description: "", source: "x" };

test("custom flags and rubric notes come from the profile", () => {
  const flags = standardFlags(opening, [], profile);
  assert.ok(flags.includes("Check the travel load"));
  assert.ok(flags.includes("Strategic or admin role?"));
  assert.ok(!standardFlags({ ...opening, title: "Engineer" }, [], profile).includes("Strategic or admin role?"));
  assert.match(rubricText(profile), /Payments focus beats generic ops\./);
});

test("server labels follow JOBKIT_LANG", () => {
  const pt = { JOBKIT_LANG: "pt-PT" } as Env;
  assert.equal(tr(pt, "Check the non-compete / outside-activities clause"), "Verificar cláusula de não concorrência / atividades externas");
  assert.match(tr(pt, "Confirm you can work remotely from Porto, Portugal (local entity, EOR or contractor)"), /^Confirmar elegibilidade remota a partir de Porto, Portugal/);
  assert.equal(tr({} as Env, "Added manually"), "Added manually");
  assert.match(langName(pt), /European Portuguese/);
});

test("stored flags are re-translated to the UI language", () => {
  assert.equal(localizeFlag("Verificar cláusula de não concorrência / atividades externas", "en"), "Check the non-compete / outside-activities clause");
  assert.equal(localizeFlag("Confirmar elegibilidade remota PT (entidade UE/EOR/contractor)", "en"), "Confirm you can work remotely from Portugal (local entity, EOR or contractor)");
  assert.equal(localizeFlag("Something custom", "en"), "Something custom");
});

test("company domains come from the company's own URLs, not job boards", () => {
  assert.equal(companyDomain("https://careers.example-co.com/jobs/1", "Example Co"), "example-co.com");
  assert.equal(companyDomain("https://apply.acme.co.uk/x", "Acme"), "acme.co.uk");
  assert.equal(companyDomain("https://jobs.ashbyhq.com/acme/123", "Acme"), "");
  assert.equal(companyDomain("https://unrelated.io/jobs", "Acme"), "");
  assert.equal(domainMap([{ company: "Acme", posting: "https://boards.greenhouse.io/acme/1" }, { company: "acme", posting: "https://acme.io/careers" }]).get("acme"), "acme.io");
});

test("wrangler.local.jsonc merges over wrangler.jsonc", () => {
  const base = parseJsonc('{ "name": "jobkit", // c\n "vars": { "A": "1", "B": "2", }, "triggers": { "crons": ["0 7 * * *"] } }');
  const out = merge(base, { name: "mine", vars: { B: "x" }, triggers: { crons: ["0 6 * * *"] } });
  assert.deepEqual(out, { name: "mine", vars: { A: "1", B: "x" }, triggers: { crons: ["0 6 * * *"] } });
});
