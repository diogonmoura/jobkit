import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { PGlite } from "@electric-sql/pglite";
import { neonConfig } from "@neondatabase/serverless";
import { applyMigrations, createPostgresDb, neonRunner, pendingMigrations, TYPE_PARSERS, type SqlRunner } from "./postgres";
import { POSTGRES_MIGRATIONS } from "./postgres-sql.generated";

const U1 = "00000000-0000-0000-0000-000000000001";
const U2 = "00000000-0000-0000-0000-000000000002";
const F1 = "00000000-0000-0000-0000-0000000000f1";
const F2 = "00000000-0000-0000-0000-0000000000f2";

function runnerFor(pg: PGlite): SqlRunner {
  return async (text, values) => (await pg.query(text, values as any[])).rows as Record<string, unknown>[];
}

const app = (id: string, extra: Record<string, unknown> = {}) => ({ id, company: "Acme", role: "PM", applied: "2026-01-01", ...extra });

let pg: PGlite;
let run: SqlRunner;
beforeEach(async () => {
  pg = new PGlite({ parsers: TYPE_PARSERS });
  run = runnerFor(pg);
  await applyMigrations(run);
});

describe("postgres driver", () => {
  it("inserts and selects back with a json column round trip", async () => {
    const db = createPostgresDb(run, {});
    const ins = await db.from("jobkit_apps").insert({ user_id: U1, ...app("a1", { events: [{ kind: "applied", n: 1 }] }) });
    assert.deepEqual(ins, { data: null, error: null });
    const res = await db.from("jobkit_apps").select("id, events").eq("id", "a1");
    assert.equal(res.error, null);
    assert.deepEqual(res.data, [{ id: "a1", events: [{ kind: "applied", n: 1 }] }]);
  });

  it("upserts twice on user_id,id and updates in place", async () => {
    const db = createPostgresDb(run, {});
    await db.from("jobkit_apps").upsert({ user_id: U1, ...app("a1") }, { onConflict: "user_id,id" });
    const second = await db.from("jobkit_apps").upsert({ user_id: U1, ...app("a1", { company: "Beta" }) }, { onConflict: "user_id,id" }).select("company");
    assert.deepEqual(second.data, [{ company: "Beta" }]);
    const all = await db.from("jobkit_apps").select("id");
    assert.equal(all.data!.length, 1);
  });

  it("conditional update returns [] on a stale updated_at and one row on the current one", async () => {
    const db = createPostgresDb(run, {});
    await db.from("jobkit_apps").insert({ user_id: U1, ...app("a1"), updated_at: "2026-02-01T00:00:00.000Z" });
    const cur = await db.from("jobkit_apps").select("updated_at").eq("id", "a1").single();
    const stale = await db.from("jobkit_apps").update({ role: "X" }).eq("id", "a1").eq("updated_at", "2020-01-01T00:00:00.000Z").select("id");
    assert.deepEqual(stale, { data: [], error: null });
    const ok = await db.from("jobkit_apps").update({ role: "X" }).eq("id", "a1").eq("updated_at", cur.data.updated_at).select("id");
    assert.equal(ok.error, null);
    assert.deepEqual(ok.data, [{ id: "a1" }]);
  });

  it("head count returns null data and the count", async () => {
    const db = createPostgresDb(run, {});
    const jobs = await db.from("jobkit_jobs").insert([
      { user_id: U1, id: "j1", company: "A", role: "r" },
      { user_id: U1, id: "j2", company: "A", role: "r" },
    ]);
    assert.equal(jobs.error, null);
    const fb = await db.from("jobkit_job_feedback").insert([
      { user_id: U1, job_id: "j1", signal: "up" },
      { user_id: U1, job_id: "j2", signal: "up" },
    ]);
    assert.equal(fb.error, null);
    const res = await db.from("jobkit_job_feedback").select("job_id", { count: "exact", head: true });
    assert.deepEqual(res, { data: null, error: null, count: 2 });
  });

  it("single errors with PGRST116 on zero or many rows, maybeSingle returns null", async () => {
    const db = createPostgresDb(run, {});
    const none = await db.from("jobkit_apps").select("id").eq("id", "nope").single();
    assert.equal(none.data, null);
    assert.equal(none.error?.code, "PGRST116");
    const maybe = await db.from("jobkit_apps").select("id").eq("id", "nope").maybeSingle();
    assert.deepEqual(maybe, { data: null, error: null });
    await db.from("jobkit_apps").insert([{ user_id: U1, ...app("a1") }, { user_id: U1, ...app("a2") }]);
    const many = await db.from("jobkit_apps").select("id").single();
    assert.equal(many.error?.code, "PGRST116");
    const manyMaybe = await db.from("jobkit_apps").select("id").maybeSingle();
    assert.equal(manyMaybe.error?.code, "PGRST116");
    const one = await db.from("jobkit_apps").select("id").eq("id", "a1").single();
    assert.deepEqual(one, { data: { id: "a1" }, error: null });
  });

  it("scopes reads and writes to the owner", async () => {
    const raw = createPostgresDb(run, {});
    await raw.from("jobkit_apps").insert([{ user_id: U2, ...app("theirs") }, { user_id: U1, ...app("mine") }]);
    const db = createPostgresDb(run, { ownerId: U1 });
    const seen = await db.from("jobkit_apps").select("id");
    assert.deepEqual(seen.data, [{ id: "mine" }]);
    await db.from("jobkit_apps").insert({ user_id: U2, ...app("sneaky") });
    await db.from("jobkit_apps").upsert({ user_id: U2, ...app("sneaky2") }, { onConflict: "user_id,id" });
    const stored = await raw.from("jobkit_apps").select("id, user_id").in("id", ["sneaky", "sneaky2"]);
    assert.deepEqual(stored.data!.map((r: any) => r.user_id), [U1, U1]);
  });

  it("an unfiltered update or delete through a scoped db only touches the owner's rows", async () => {
    const raw = createPostgresDb(run, {});
    await raw.from("jobkit_apps").insert([{ user_id: U2, ...app("theirs") }, { user_id: U1, ...app("mine") }]);
    const db = createPostgresDb(run, { ownerId: U1 });
    await db.from("jobkit_apps").update({ role: "Changed" });
    const roles = await raw.from("jobkit_apps").select("id, role").order("id");
    assert.deepEqual(roles.data, [{ id: "mine", role: "Changed" }, { id: "theirs", role: "PM" }]);
    const del = await db.from("jobkit_apps").delete().select("id");
    assert.deepEqual(del.data, [{ id: "mine" }]);
    const left = await raw.from("jobkit_apps").select("id");
    assert.deepEqual(left.data, [{ id: "theirs" }]);
  });

  it("does not scope tables without an owner column", async () => {
    const db = createPostgresDb(run, { ownerId: U1 });
    const ins = await db.from("jobkit_instance").insert({ id: 1, owner_user_id: U2 });
    assert.equal(ins.error, null);
    const res = await db.from("jobkit_instance").select("owner_user_id").maybeSingle();
    assert.deepEqual(res.data, { owner_user_id: U2 });
  });

  it("returns SQL and compile errors as results", async () => {
    const db = createPostgresDb(run, {});
    const dup = await db.from("jobkit_apps").insert({ user_id: U1, ...app("a1") });
    assert.equal(dup.error, null);
    const again = await db.from("jobkit_apps").insert({ user_id: U1, ...app("a1") });
    assert.equal(again.data, null);
    assert.equal(again.error?.code, "23505");
    const bad = await db.from("nope").select("id");
    assert.equal(bad.error?.code, "PGRST100");
  });
});

describe("owner-scoped upsert", () => {
  const file = (id: string, user_id: string, content: string) => ({ id, user_id, filename: "f.md", size: 1, content });
  it("cannot take over another owner's row on a table keyed by id alone", async () => {
    const raw = createPostgresDb(run, {});
    const ins = await raw.from("jobkit_experience_files").insert(file(F1, U2, "secret"));
    assert.equal(ins.error, null);
    const db = createPostgresDb(run, { ownerId: U1 });
    const res = await db.from("jobkit_experience_files").upsert(file(F1, U1, "mine"), { onConflict: "id" }).select("id, content, user_id");
    assert.deepEqual(res.data, []);
    const after = await raw.from("jobkit_experience_files").select("user_id, content").eq("id", F1).single();
    assert.deepEqual(after.data, { user_id: U2, content: "secret" });
  });
  it("still updates the owner's own row and keeps user_id", async () => {
    const db = createPostgresDb(run, { ownerId: U1 });
    await db.from("jobkit_experience_files").insert(file(F2, U1, "v1"));
    const res = await db.from("jobkit_experience_files").upsert(file(F2, U2, "v2"), { onConflict: "id" }).select("content, user_id");
    assert.deepEqual(res.data, [{ content: "v2", user_id: U1 }]);
  });
});

describe("value shapes", () => {
  it("keeps timestamps as microsecond text so a conditional update matches", async () => {
    const db = createPostgresDb(run, {});
    await db.from("jobkit_apps").insert({ user_id: U1, ...app("a1"), updated_at: "2026-02-01 00:00:00.123456+00" });
    const row = await db.from("jobkit_apps").select("updated_at, applied").eq("id", "a1").single();
    assert.equal(typeof row.data.updated_at, "string");
    assert.match(row.data.updated_at, /\.123456/);
    assert.equal(row.data.applied, "2026-01-01");
    const ok = await db.from("jobkit_apps").update({ role: "Z" }).eq("id", "a1").eq("updated_at", row.data.updated_at).select("id");
    assert.deepEqual(ok.data, [{ id: "a1" }]);
  });
  it("returns bigint counts as numbers", async () => {
    const rows = await run("select 5::bigint as a, 1.5::numeric as b", []);
    assert.deepEqual(rows, [{ a: 5, b: 1.5 }]);
  });
});

describe("neonRunner", () => {
  it("applies the shared type parsers to the HTTP driver's results", async () => {
    const original = neonConfig.fetchFunction;
    neonConfig.fetchFunction = async () =>
      new Response(
        JSON.stringify({
          command: "SELECT",
          rowCount: 1,
          fields: [
            { name: "ts", dataTypeID: 1184 },
            { name: "n", dataTypeID: 20 },
            { name: "b", dataTypeID: 16 },
            { name: "i", dataTypeID: 23 },
            { name: "j", dataTypeID: 3802 },
          ],
          rows: [["2026-02-01 00:00:00.123456+00", "5", "f", "5", '{"a":1}']],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    try {
      const rows = await neonRunner("postgres://u:p@example.neon.tech/db")("select 1", []);
      assert.deepEqual(rows, [{ ts: "2026-02-01 00:00:00.123456+00", n: 5, b: false, i: 5, j: { a: 1 } }]);
    } finally {
      neonConfig.fetchFunction = original;
    }
  });
});

describe("migrations", () => {
  it("lists the baseline as pending before applyMigrations and nothing after", async () => {
    const fresh = runnerFor(new PGlite());
    assert.deepEqual(await pendingMigrations(fresh), POSTGRES_MIGRATIONS.map((m) => m.id));
    assert.deepEqual(await applyMigrations(fresh), POSTGRES_MIGRATIONS.map((m) => m.id));
    assert.deepEqual(await pendingMigrations(fresh), []);
    assert.deepEqual(await applyMigrations(fresh), []);
  });
});
