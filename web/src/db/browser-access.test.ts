import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { BROWSER_ACCESS } from "./browser-access";
import { handleDbApi } from "./api";
import { applyMigrations, TYPE_PARSERS, type SqlRunner } from "./postgres";
import { TABLES } from "./tables";

const root = path.resolve(import.meta.dirname, "../..");
const ALL = ["select", "insert", "update", "upsert", "delete"];
const U1 = "00000000-0000-0000-0000-000000000001";
const U2 = "00000000-0000-0000-0000-000000000002";

function supabaseSql(): string {
  const dir = path.join(root, "supabase/migrations");
  const files = fs.readdirSync(dir).filter((f) => f.endsWith(".sql")).map((f) => path.join(dir, f));
  return [path.join(root, "supabase/schema.sql"), ...files].map((f) => fs.readFileSync(f, "utf8").replace(/--.*$/gm, "")).join("\n");
}

describe("BROWSER_ACCESS", () => {
  it("mirrors the Supabase RLS policies", () => {
    const sql = supabaseSql();
    const expected: Record<string, Set<string>> = {};
    for (const m of sql.matchAll(/create policy\s+"[^"]+"\s+on\s+(?:public\.)?(jobkit_\w+)\s+for\s+(all|select|insert|update|delete)\b/gi)) {
      (expected[m[1]] ??= new Set()).add(m[2].toLowerCase());
    }
    const rls = [...sql.matchAll(/alter table\s+(?:public\.)?(jobkit_\w+)\s+enable row level security/gi)].map((m) => m[1]);
    assert.ok(rls.length >= 14 && Object.keys(expected).length >= 10, "parsed the policies");
    for (const table of rls) {
      const ops = expected[table];
      if (!ops) {
        assert.equal(Object.hasOwn(BROWSER_ACCESS, table), false, `${table} has RLS and no policy`);
      } else if (ops.has("all")) {
        assert.deepEqual([...BROWSER_ACCESS[table]].sort(), [...ALL].sort(), table);
      } else {
        assert.deepEqual([...BROWSER_ACCESS[table]].sort(), [...ops].sort(), table);
      }
    }
    for (const table of Object.keys(BROWSER_ACCESS)) assert.ok(rls.includes(table), `${table} is in BROWSER_ACCESS without RLS`);
    for (const table of Object.keys(BROWSER_ACCESS)) assert.ok(Object.hasOwn(TABLES, table), table);
  });
});

describe("handleDbApi", () => {
  let pg: PGlite;
  let run: SqlRunner;
  beforeEach(async () => {
    pg = new PGlite({ parsers: TYPE_PARSERS });
    run = async (text, values) => (await pg.query(text, values as any[])).rows as Record<string, unknown>[];
    await applyMigrations(run);
  });

  const call = (plan: unknown, headers: Record<string, string> = { "Content-Type": "application/json" }) =>
    handleDbApi(new Request("https://x/api/db", { method: "POST", headers, body: typeof plan === "string" ? plan : JSON.stringify(plan) }), {} as any, U1, run);
  const plan = (extra: Record<string, unknown>) => ({ table: "jobkit_apps", op: "select", filters: [], order: [], ...extra });
  const app = (id: string, user_id: string) => ({ user_id, id, company: "Acme", role: "PM", applied: "2026-01-01" });

  it("refuses tables and ops outside the allowlist with 403", async () => {
    assert.equal((await call(plan({ table: "jobkit_settings" }))).status, 403);
    assert.equal((await call(plan({ table: "jobkit_salary_cache", op: "delete" }))).status, 403);
    assert.equal((await call(plan({ table: "jobkit_scan_runs", op: "update", patch: { a: 1 } }))).status, 403);
    assert.equal((await call(plan({ table: "constructor" }))).status, 403);
  });

  it("returns only the caller's rows", async () => {
    await run("insert into jobkit_apps (user_id, id, company, role, applied) values ($1,'a1','A','r','2026-01-01'), ($2,'a2','B','r','2026-01-01')", [U1, U2]);
    const res = await call(plan({ columns: "id" }));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { data: [{ id: "a1" }], error: null });
  });

  it("stores an insert naming another user under the caller", async () => {
    const res = await call(plan({ op: "insert", rows: [app("a3", U2)] }));
    assert.equal(res.status, 200);
    const rows = await run("select user_id from jobkit_apps where id = 'a3'", []);
    assert.deepEqual(rows, [{ user_id: U1 }]);
  });

  it("ignores a conflictOwner sent by the client", async () => {
    await run("insert into jobkit_apps (user_id, id, company, role, applied) values ($1,'a9','Theirs','r','2026-01-01')", [U2]);
    const res = await call(plan({ op: "upsert", rows: [app("a9", U2)], onConflict: "user_id,id", conflictOwner: U2 }));
    assert.equal(res.status, 200);
    const rows = await run("select user_id, company from jobkit_apps where id = 'a9' order by user_id", []);
    assert.deepEqual(rows, [{ user_id: U1, company: "Acme" }, { user_id: U2, company: "Theirs" }]);
  });

  it("rejects non-JSON content types, bad JSON and malformed plans", async () => {
    assert.equal((await call(plan({}), { "Content-Type": "text/plain" })).status, 415);
    assert.equal((await call(plan({}), {})).status, 415);
    assert.equal((await call("{nope")).status, 400);
    assert.equal((await call([])).status, 400);
    assert.equal((await call(plan({ extra: 1 }))).status, 400);
    assert.equal((await call(plan({ op: "drop" }))).status, 400);
    assert.equal((await call(plan({ table: 5 }))).status, 400);
    assert.equal((await call(plan({ rows: "x" }))).status, 400);
    assert.equal((await call(plan({ filters: [{ column: "id", op: "like", value: "a" }] }))).status, 400);
    assert.equal((await call(plan({ filters: [{ column: "id", op: "in", value: "a" }] }))).status, 400);
    assert.equal((await call(plan({ filters: undefined }))).status, 400);
    assert.equal((await call(plan({ order: [{ column: "id" }] }))).status, 400);
  });

  it("maps a compile error to 400", async () => {
    assert.equal((await call(plan({ columns: "nope" }))).status, 400);
  });
});
