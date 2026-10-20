import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { dedupeOpenings, prefilterOpenings } from "./prefilter";
import type { PreferenceProfile, RawOpening } from "../types";
import { extractPublishedSalary } from "../salary";

const profile: PreferenceProfile = {
  roles: ["engineer"],
  seniority: ["senior"],
  industries: [],
  locations: ["portugal"],
  remote: true,
  companies: [],
  keywords: ["typescript"],
  keywords_exclude: ["sales"],
  salary_floor: { amount: 0, currency: "EUR", period: "year" },
};

const sample: RawOpening = {
  externalKey: "x:1",
  company: "Acme",
  title: "Senior TypeScript Engineer",
  url: "https://example.com/1",
  location: "Remote, Portugal",
  description: "Build APIs with TypeScript",
  source: "scan",
};

describe("prefilterOpenings", () => {
  it("keeps matching keywords and drops excluded", () => {
    const kept = prefilterOpenings([sample], profile);
    assert.equal(kept.length, 1);
    const dropped = prefilterOpenings(
      [{ ...sample, title: "Enterprise Sales Lead" }],
      profile,
    );
    assert.equal(dropped.length, 0);
  });
});

describe("location rules", () => {
  const home: PreferenceProfile = {
    ...profile,
    locations: [],
    home_base: { city: "Porto", country: "Portugal", region: "EU" },
  };
  it("keeps remote roles open to the home region and drops other regions", () => {
    assert.equal(prefilterOpenings([{ ...sample, location: "Remote - Europe" }], home).length, 1);
    assert.equal(prefilterOpenings([{ ...sample, location: "Remote - US only" }], home).length, 0);
  });
  it("keeps on-site roles in the home city", () => {
    assert.equal(prefilterOpenings([{ ...sample, location: "Porto, Portugal" }], { ...home, remote: false }).length, 1);
  });
  it("remote_policy any skips the location filter", () => {
    assert.equal(prefilterOpenings([{ ...sample, location: "Austin, TX" }], { ...home, remote_policy: "any" }).length, 1);
  });
});

describe("dedupeOpenings", () => {
  it("skips existing external keys and urls", () => {
    const keys = new Set(["x:1"]);
    const urls = new Set(["https://example.com/2"]);
    const list = [
      sample,
      { ...sample, externalKey: "x:1", url: "https://example.com/9" },
      { ...sample, externalKey: "x:2", url: "https://example.com/2" },
      { ...sample, externalKey: "x:3", url: "https://example.com/3" },
    ];
    const out = dedupeOpenings(list, keys, urls);
    assert.equal(out.length, 1);
    assert.equal(out[0].externalKey, "x:3");
  });
});

describe("extractPublishedSalary", () => {
  it("parses euro range from text", () => {
    const pub = extractPublishedSalary("Salary: €45,000 - €55,000 per year");
    assert.ok(pub);
    assert.equal(pub?.currency, "EUR");
    assert.ok((pub?.min ?? 0) >= 45000);
  });
});
