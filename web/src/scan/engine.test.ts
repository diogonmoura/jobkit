import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { clearEngineOpenings, engineOpening, loadEngineOpenings } from "./engine";

const ROW = {
  external_key: "successfactors:jobs.edp.com:1234",
  company: "EDP",
  title: "Head of Strategy",
  url: "https://jobs.edp.com/job/1234",
  description: "Lead strategy.",
  location: "Lisbon, PT",
  source: "successfactors:jobs.edp.com",
};

/** Just enough of the Supabase query builder for these calls; records what was asked. */
function fakeSupabase(rows: unknown[]) {
  const calls: { table: string; op: string; filters: [string, unknown][] }[] = [];
  const builder = (table: string, op: string) => {
    const call = { table, op, filters: [] as [string, unknown][] };
    calls.push(call);
    const b = {
      eq(col: string, val: unknown) { call.filters.push([col, val]); return b; },
      in(col: string, val: unknown) { call.filters.push([col, val]); return b; },
      limit() { return b; },
      then(resolve: (v: unknown) => void) { resolve(op === "select" ? { data: rows, error: null } : { error: null }); },
    };
    return b;
  };
  return {
    calls,
    from: (table: string) => ({ select: () => builder(table, "select"), delete: () => builder(table, "delete") }),
  };
}

describe("engineOpening", () => {
  it("maps a waiting-table row to a scan opening", () => {
    assert.deepEqual(engineOpening(ROW), {
      externalKey: "successfactors:jobs.edp.com:1234",
      company: "EDP",
      title: "Head of Strategy",
      url: "https://jobs.edp.com/job/1234",
      location: "Lisbon, PT",
      description: "Lead strategy.",
      source: "engine",
      vendor: "successfactors",
    });
  });

  it("tolerates missing optional fields", () => {
    const o = engineOpening({ external_key: "k", company: "C", title: "T", url: "u" });
    assert.equal(o.description, "");
    assert.equal(o.location, "");
    assert.equal(o.vendor, "engine");
  });
});

describe("loadEngineOpenings / clearEngineOpenings", () => {
  it("reads the user's rows and deletes exactly the ones read", async () => {
    const sb = fakeSupabase([ROW]);
    const openings = await loadEngineOpenings(sb as never, "user-1");
    assert.equal(openings.length, 1);
    await clearEngineOpenings(sb as never, "user-1", openings);
    const del = sb.calls.find((c) => c.op === "delete")!;
    assert.equal(del.table, "jobkit_external_openings");
    assert.deepEqual(del.filters, [["user_id", "user-1"], ["external_key", ["successfactors:jobs.edp.com:1234"]]]);
  });

  it("does nothing when there was nothing to read", async () => {
    const sb = fakeSupabase([]);
    await clearEngineOpenings(sb as never, "user-1", []);
    assert.equal(sb.calls.length, 0);
  });
});

describe("setup SQL", () => {
  it("creates the engine bridge tables with row level security", async () => {
    const { readFileSync } = await import("node:fs");
    const sql = readFileSync(new URL("../../public/setup/jobkit-schema.sql", import.meta.url), "utf8");
    for (const table of ["jobkit_external_openings", "jobkit_sync_state"]) {
      assert.match(sql, new RegExp(`create table if not exists public\\.${table}`));
      assert.match(sql, new RegExp(`alter table public\\.${table} enable row level security`));
    }
  });
});

describe("jobkit_apps updated_at", () => {
  it("moves on every update, so the engine's sync can tell a web edit happened", async () => {
    const { readFileSync } = await import("node:fs");
    const sql = readFileSync(new URL("../../public/setup/jobkit-schema.sql", import.meta.url), "utf8");
    assert.match(sql, /create trigger jobkit_apps_touch before update on public\.jobkit_apps/);
    assert.match(sql, /drop trigger if exists jobkit_apps_touch on public\.jobkit_apps/);
  });
});
