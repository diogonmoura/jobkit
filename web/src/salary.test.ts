import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { extractPublishedSalary } from "./salary";

describe("extractPublishedSalary", () => {
  it("reads a published range", () => {
    const salary = extractPublishedSalary("Salary: €60,000 - €80,000 per year");
    assert.equal(salary?.min, 60000);
    assert.equal(salary?.max, 80000);
    assert.equal(salary?.currency, "EUR");
  });

  it("stays fast on long runs of digits and spaces", () => {
    const text = "salary " + "1 ".repeat(20000) + "x";
    const start = Date.now();
    extractPublishedSalary(text);
    assert.ok(Date.now() - start < 500, "took too long");
  });
});
