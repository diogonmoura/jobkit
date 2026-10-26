import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { PGlite } from "@electric-sql/pglite";
import { applyMigrations, createPostgresDb, pendingMigrations, type SqlRunner } from "./postgres";
import { POSTGRES_MIGRATIONS } from "./postgres-sql.generated";

const U1 = "00000000-0000-0000-0000-000000000001";
const U2 = "00000000-0000-0000-0000-000000000002";

function runnerFor(pg: PGlite): SqlRunner {
  return async (text, values) => (await pg.query(text, values as any[])).rows as Record<string, unknown>[];
}

const app = (id: string, extra: Record<string, unknown> = {}) => ({ id, company: "Acme", role: "PM", applied: "2026-01-01", ...extra });

let pg: PGlite;
let run: SqlRunner;
beforeEach(async () => {
  pg = new PGlite();
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

describe("migrations", () => {
  it("lists the baseline as pending before applyMigrations and nothing after", async () => {
    const fresh = runnerFor(new PGlite());
    assert.deepEqual(await pendingMigrations(fresh), POSTGRES_MIGRATIONS.map((m) => m.id));
    assert.deepEqual(await applyMigrations(fresh), POSTGRES_MIGRATIONS.map((m) => m.id));
    assert.deepEqual(await pendingMigrations(fresh), []);
    assert.deepEqual(await applyMigrations(fresh), []);
  });
});
