import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { compile, DbCompileError } from "./compile";
import type { QueryPlan, TableSpec } from "./types";

const tables: Record<string, TableSpec> = {
  jobkit_apps: { columns: ["user_id", "id", "company", "status", "payload", "updated_at"], jsonColumns: ["payload"], owned: true },
  jobkit_salary_cache: { columns: ["cache_key", "payload"], jsonColumns: ["payload"], owned: false },
};

const plan = (p: Partial<QueryPlan>): QueryPlan => ({ table: "jobkit_apps", op: "select", filters: [], order: [], ...p });

describe("compile select", () => {
  it("compiles columns, filter, order and limit", () => {
    const out = compile(
      plan({
        columns: "id, company",
        filters: [{ column: "user_id", op: "eq", value: "u1" }],
        order: [{ column: "updated_at", ascending: false }],
        limit: 5,
      }),
      tables,
    );
    assert.equal(out.text, 'select "id", "company" from "jobkit_apps" where "user_id" = $1 order by "updated_at" desc limit 5');
    assert.deepEqual(out.values, ["u1"]);
  });

  it("selects * when no columns are given", () => {
    assert.equal(compile(plan({}), tables).text, 'select * from "jobkit_apps"');
    assert.equal(compile(plan({ columns: "*" }), tables).text, 'select * from "jobkit_apps"');
  });

  it("compiles a head count", () => {
    const out = compile(plan({ count: "exact", head: true, filters: [{ column: "user_id", op: "eq", value: "u1" }] }), tables);
    assert.equal(out.text, 'select count(*)::int as count from "jobkit_apps" where "user_id" = $1');
    assert.deepEqual(out.values, ["u1"]);
  });

  it("rejects a count without head", () => {
    assert.throws(() => compile(plan({ count: "exact" }), tables), DbCompileError);
  });

  it("compiles in as any() with one array parameter", () => {
    const out = compile(plan({ filters: [{ column: "id", op: "in", value: ["a", "b"] }] }), tables);
    assert.equal(out.text, 'select * from "jobkit_apps" where "id" = any($1)');
    assert.deepEqual(out.values, [["a", "b"]]);
  });

  it("compiles gte and joins filters with and", () => {
    const out = compile(
      plan({ filters: [{ column: "status", op: "eq", value: "open" }, { column: "updated_at", op: "gte", value: "2026-01-01" }] }),
      tables,
    );
    assert.equal(out.text, 'select * from "jobkit_apps" where "status" = $1 and "updated_at" >= $2');
    assert.deepEqual(out.values, ["open", "2026-01-01"]);
  });

  it("compiles nulls first", () => {
    const out = compile(plan({ order: [{ column: "updated_at", ascending: true, nullsFirst: true }] }), tables);
    assert.equal(out.text, 'select * from "jobkit_apps" order by "updated_at" asc nulls first');
  });
});

describe("compile mutations", () => {
  it("inserts over the union of keys, using default for missing ones", () => {
    const out = compile(
      plan({ op: "insert", rows: [{ user_id: "u1", id: "a" }, { user_id: "u1", company: "Acme" }] }),
      tables,
    );
    assert.equal(
      out.text,
      'insert into "jobkit_apps" ("user_id", "id", "company") values ($1, $2, default), ($3, default, $4)',
    );
    assert.deepEqual(out.values, ["u1", "a", "u1", "Acme"]);
  });

  it("emits returning on insert", () => {
    const out = compile(plan({ op: "insert", rows: [{ user_id: "u1", id: "a" }], returning: "id" }), tables);
    assert.equal(out.text, 'insert into "jobkit_apps" ("user_id", "id") values ($1, $2) returning "id"');
  });

  it("upserts with on conflict do update", () => {
    const out = compile(
      plan({ op: "upsert", onConflict: "user_id,id", rows: [{ user_id: "u1", id: "a", company: "Acme", status: "open" }] }),
      tables,
    );
    assert.equal(
      out.text,
      'insert into "jobkit_apps" ("user_id", "id", "company", "status") values ($1, $2, $3, $4) ' +
        'on conflict ("user_id", "id") do update set "company" = excluded."company", "status" = excluded."status"',
    );
    assert.deepEqual(out.values, ["u1", "a", "Acme", "open"]);
  });

  it("upserts with do nothing when every column is a conflict column", () => {
    const out = compile(plan({ op: "upsert", onConflict: "user_id,id", rows: [{ user_id: "u1", id: "a" }] }), tables);
    assert.match(out.text, /on conflict \("user_id", "id"\) do nothing$/);
  });

  it("rejects an upsert without onConflict", () => {
    assert.throws(() => compile(plan({ op: "upsert", rows: [{ id: "a" }] }), tables), DbCompileError);
  });

  it("updates with filters and returning", () => {
    const out = compile(
      plan({
        op: "update",
        patch: { status: "closed", company: "Acme" },
        filters: [{ column: "user_id", op: "eq", value: "u1" }, { column: "id", op: "eq", value: "a" }],
        returning: "id",
      }),
      tables,
    );
    assert.equal(
      out.text,
      'update "jobkit_apps" set "status" = $1, "company" = $2 where "user_id" = $3 and "id" = $4 returning "id"',
    );
    assert.deepEqual(out.values, ["closed", "Acme", "u1", "a"]);
  });

  it("deletes with filters and returning", () => {
    const out = compile(
      plan({ op: "delete", filters: [{ column: "id", op: "eq", value: "a" }], returning: "id, company" }),
      tables,
    );
    assert.equal(out.text, 'delete from "jobkit_apps" where "id" = $1 returning "id", "company"');
  });

  it("rejects an empty insert or patch", () => {
    assert.throws(() => compile(plan({ op: "insert", rows: [] }), tables), DbCompileError);
    assert.throws(() => compile(plan({ op: "update", patch: {} }), tables), DbCompileError);
  });
});

describe("compile json columns", () => {
  it("stringifies values and casts to jsonb", () => {
    const out = compile(plan({ op: "insert", rows: [{ id: "a", payload: { a: [1, 2] } }] }), tables);
    assert.equal(out.text, 'insert into "jobkit_apps" ("id", "payload") values ($1, $2::jsonb)');
    assert.deepEqual(out.values, ["a", '{"a":[1,2]}']);
  });

  it("casts in update and upsert, and keeps null as SQL null", () => {
    const up = compile(plan({ op: "update", patch: { payload: { x: 1 } } }), tables);
    assert.equal(up.text, 'update "jobkit_apps" set "payload" = $1::jsonb');
    assert.deepEqual(up.values, ['{"x":1}']);
    const nul = compile(plan({ op: "update", patch: { payload: null } }), tables);
    assert.equal(nul.text, 'update "jobkit_apps" set "payload" = $1');
    assert.deepEqual(nul.values, [null]);
  });
});

describe("compile allowlist", () => {
  it("rejects an unknown table", () => {
    assert.throws(() => compile(plan({ table: "users" }), tables), DbCompileError);
  });

  it("rejects an unknown column everywhere", () => {
    assert.throws(() => compile(plan({ columns: "id, nope" }), tables), DbCompileError);
    assert.throws(() => compile(plan({ filters: [{ column: "nope", op: "eq", value: 1 }] }), tables), DbCompileError);
    assert.throws(() => compile(plan({ order: [{ column: "nope", ascending: true }] }), tables), DbCompileError);
    assert.throws(() => compile(plan({ op: "update", patch: { nope: 1 } }), tables), DbCompileError);
    assert.throws(() => compile(plan({ op: "insert", rows: [{ nope: 1 }] }), tables), DbCompileError);
    assert.throws(() => compile(plan({ op: "upsert", onConflict: "id,nope", rows: [{ id: "a" }] }), tables), DbCompileError);
    assert.throws(() => compile(plan({ op: "insert", rows: [{ id: "a" }], returning: "nope" }), tables), DbCompileError);
  });

  it("rejects an injection attempt in a column name", () => {
    assert.throws(() => compile(plan({ columns: 'id" from x; --' }), tables), DbCompileError);
  });
});
