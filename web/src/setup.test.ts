import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { parseBoardUrl, profileFromAnswers } from "./setup";
import { EMPTY_PROFILE } from "./preferences";

describe("parseBoardUrl", () => {
  it("recognises supported ATS links", () => {
    assert.deepEqual(parseBoardUrl("https://jobs.ashbyhq.com/acme"), { vendor: "ashby", slug: "acme" });
    assert.deepEqual(parseBoardUrl("https://boards.greenhouse.io/acme/jobs/1"), { vendor: "greenhouse", slug: "acme" });
    assert.deepEqual(parseBoardUrl("https://job-boards.greenhouse.io/embed/job_board?for=acme"), { vendor: "greenhouse", slug: "acme" });
    assert.deepEqual(parseBoardUrl("https://jobs.lever.co/acme"), { vendor: "lever", slug: "acme" });
    assert.deepEqual(parseBoardUrl("https://apply.workable.com/acme/"), { vendor: "workable", slug: "acme" });
    assert.equal(parseBoardUrl("https://example.com/careers"), null);
  });
});

describe("profileFromAnswers", () => {
  it("turns wizard answers into profile, filters and rubric", () => {
    const { profile, candidate } = profileFromAnswers(
      {
        tiers: { T1: "Staff Engineer\nPrincipal Engineer", T2: "Senior Engineer" },
        home_city: "Porto",
        home_country: "Portugal",
        remote_policy: "remote_or_hybrid",
        avoid: { consultancies: "exclude", it_services: "flag" },
        salary_amount: 70000,
        salary_currency: "eur",
        rubric: { tier: 40, remote: 20, focus: 20, seniority: 10, company: 10 },
        min_score: 60,
      },
      EMPTY_PROFILE,
    );
    assert.deepEqual(profile.title_tiers?.T1, ["Staff Engineer", "Principal Engineer"]);
    assert.equal(profile.roles.length, 3);
    assert.ok(profile.locations.includes("Porto"));
    assert.equal(profile.remote_policy, "remote_or_hybrid");
    assert.ok(profile.exclude_companies?.includes("Accenture"));
    assert.ok(profile.flag_companies?.includes("outsourcing"));
    assert.equal(profile.salary_floor.currency, "EUR");
    assert.equal(profile.min_score, 60);
    assert.equal(profile.rubric?.tier, 40);
    assert.match(candidate, /Best-fit roles/);
  });
});
