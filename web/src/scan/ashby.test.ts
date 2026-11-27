import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { ashbyLocation, ashbyLocationText, fetchAshby } from "./sources";
import { prefilterOpenings } from "./prefilter";
import type { PreferenceProfile } from "../types";

// Shaped like n8n's board: every role sits under "Berlin Office"; Portugal appears only as a secondary location.
const n8nJob = {
  id: "0b1c2d3e-0000-4000-8000-000000000001",
  title: "Senior Product Manager",
  jobUrl: "https://jobs.ashbyhq.com/n8n/0b1c2d3e-0000-4000-8000-000000000001",
  location: "Berlin Office",
  isRemote: false,
  workplaceType: "Hybrid",
  secondaryLocations: [
    { location: "Germany", address: { postalAddress: { addressCountry: "Germany" } } },
    { location: "Lisbon", address: { postalAddress: { addressCountry: "Portugal" } } },
  ],
  descriptionPlain: "Own the roadmap.",
};

const n8nGraphqlJob = {
  id: "0b1c2d3e-0000-4000-8000-000000000002",
  title: "Senior Product Manager",
  locationName: "Berlin Office",
  workplaceType: "Remote",
  secondaryLocations: [{ locationName: "Germany" }, { locationName: "Portugal" }],
};

const profile: PreferenceProfile = {
  roles: ["product manager"],
  seniority: ["senior"],
  industries: [],
  locations: ["Portugal", "Lisbon", "Lisboa", "Porto"],
  remote: false,
  companies: [],
  keywords: [],
  keywords_exclude: [],
  salary_floor: { amount: 0, currency: "EUR", period: "year" },
};

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function stubFetch(handler: (url: string, init?: RequestInit) => unknown) {
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const body = handler(String(input), init);
    return body === undefined
      ? new Response("not found", { status: 404 })
      : new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

describe("Ashby secondary locations", () => {
  it("keeps the displayed location to the primary place", () => {
    assert.equal(ashbyLocation(n8nJob), "Berlin Office");
  });

  it("lists secondary places and countries in the match text", () => {
    assert.equal(ashbyLocationText(n8nJob), "Berlin Office / Germany / Lisbon / Portugal");
  });

  it("treats workplaceType Remote as remote", () => {
    assert.equal(ashbyLocation({ location: "Berlin Office", workplaceType: "Remote" }), "Berlin Office (Remote)");
    assert.equal(ashbyLocationText(n8nGraphqlJob), "Berlin Office / Germany / Portugal (Remote)");
  });

  it("a Portugal-only watch keeps an n8n role from the JSON API", async () => {
    stubFetch((url) => (url.includes("api.ashbyhq.com/posting-api/job-board/n8n") ? { jobs: [n8nJob] } : undefined));
    const openings = await fetchAshby({ company: "n8n", vendor: "ashby", slug: "n8n" });
    assert.equal(openings.length, 1);
    assert.equal(openings[0].location, "Berlin Office");
    assert.equal(prefilterOpenings(openings, profile).length, 1);
    // Without the secondary locations the same role is dropped, which is the bug this fixes.
    assert.equal(prefilterOpenings([{ ...openings[0], locationText: undefined }], profile).length, 0);
  });

  it("a Portugal-only watch keeps an n8n role from the GraphQL fallback", async () => {
    let query = "";
    stubFetch((url, init) => {
      if (url.includes("non-user-graphql")) {
        query = String(JSON.parse(String(init?.body)).query);
        return { data: { jobBoard: { jobPostings: [n8nGraphqlJob] } } };
      }
      return undefined;
    });
    const openings = await fetchAshby({ company: "n8n", vendor: "ashby", slug: "n8n" });
    assert.match(query, /secondaryLocations \{ locationName \}/);
    assert.match(query, /workplaceType/);
    assert.equal(openings[0].location, "Berlin Office (Remote)");
    assert.equal(prefilterOpenings(openings, profile).length, 1);
  });

  it("still drops a Berlin-only role for a Portugal-only watch", async () => {
    stubFetch(() => ({ jobs: [{ ...n8nJob, secondaryLocations: [] }] }));
    const openings = await fetchAshby({ company: "n8n", vendor: "ashby", slug: "n8n" });
    assert.equal(prefilterOpenings(openings, profile).length, 0);
  });
});
