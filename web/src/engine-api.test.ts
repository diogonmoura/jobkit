import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { PGlite } from "@electric-sql/pglite";
import { handleEngineApi } from "./engine-api";
import { applyMigrations, createPostgresDb, TYPE_PARSERS, type SqlRunner } from "./db/postgres";
import type { Db } from "./db/types";

const U1 = "00000000-0000-0000-0000-000000000001";
const U2 = "00000000-0000-0000-0000-000000000002";
const env = { JOBKIT_ENGINE_TOKEN: "t", JOBKIT_OWNER_USER_ID: U1 } as any;

describe("handleEngineApi", () => {
  let run: SqlRunner;
  let db: Db;
  beforeEach(async () => {
    const pg = new PGlite({ parsers: TYPE_PARSERS });
    run = async (text, values) => (await pg.query(text, values as any[])).rows as Record<string, unknown>[];
    await applyMigrations(run);
    db = createPostgresDb(run, {});
  });

  const call = (method: string, path: string, body?: unknown, token: string | null = "t", e: any = env) => {
    const headers: Record<string, string> = {};
    if (token !== null) headers.Authorization = `Bearer ${token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    return handleEngineApi(
      new Request("https://x/api" + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }),
      e,
      path.split("?")[0],
      db,
    ) as Promise<Response>;
  };
  const app = { id: "a1", company: "Acme", role: "PM", applied: "2026-01-01", status: "applied", events: [{ d: "2026-01-02", t: "call" }] };
  const listed = async () => ((await (await call("GET", "/engine/apps")).json()) as any).rows as any[];

  it("ignores paths outside /engine", async () => {
    assert.equal(await handleEngineApi(new Request("https://x/api/db"), env, "/db", db), null);
  });

  it("rejects a missing, wrong or unconfigured token with 401", async () => {
    assert.equal((await call("GET", "/engine/owner", undefined, null)).status, 401);
    assert.equal((await call("GET", "/engine/owner", undefined, "nope")).status, 401);
    assert.equal((await call("GET", "/engine/owner", undefined, "")).status, 401);
    assert.equal((await call("GET", "/engine/apps", undefined, "t", { JOBKIT_OWNER_USER_ID: U1 })).status, 401);
    assert.equal((await call("GET", "/engine/apps", undefined, "undefined", { JOBKIT_OWNER_USER_ID: U1 })).status, 401);
  });

  it("returns the owner, or 404 before there is one", async () => {
    const res = await call("GET", "/engine/owner");
    assert.deepEqual(await res.json(), { ownerId: U1 });
    const none = await call("GET", "/engine/owner", undefined, "t", { JOBKIT_ENGINE_TOKEN: "t" });
    assert.equal(none.status, 404);
    assert.ok(typeof ((await none.json()) as any).error === "string");
  });

  it("inserts an app and lists it as a raw row", async () => {
    const res = await call("POST", "/engine/apps", app);
    assert.equal(res.status, 201);
    const rows = await listed();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].id, "a1");
    assert.equal(rows[0].user_id, U1);
    assert.equal(rows[0].company, "Acme");
    assert.deepEqual(rows[0].events, [{ d: "2026-01-02", t: "call" }]);
    assert.equal(typeof rows[0].updated_at, "string");
  });

  it("rejects unknown fields with 400 and ignores a user_id in the body", async () => {
    assert.equal((await call("POST", "/engine/apps", { ...app, bogus: 1 })).status, 400);
    assert.equal((await call("PATCH", "/engine/apps/a1?updated_at=x", { bogus: 1 })).status, 400);
    assert.equal((await call("POST", "/engine/openings", { rows: [{ external_key: "k", bogus: 1 }] })).status, 400);
    assert.equal((await call("POST", "/engine/apps", { ...app, user_id: U2 })).status, 201);
    assert.deepEqual(await run("select user_id from jobkit_apps where id = 'a1'", []), [{ user_id: U1 }]);
    await call("POST", "/engine/openings", { rows: [{ external_key: "k", user_id: U2, company: "C", title: "T", url: "u" }] });
    assert.deepEqual(await run("select user_id from jobkit_external_openings", []), [{ user_id: U1 }]);
  });

  it("updates only while updated_at still matches", async () => {
    await call("POST", "/engine/apps", app);
    const before = (await listed())[0].updated_at as string;
    const stale = await call("PATCH", `/engine/apps/a1?updated_at=${encodeURIComponent("2000-01-01 00:00:00+00")}`, { status: "interview" });
    assert.deepEqual(await stale.json(), { updated: false });
    assert.equal((await listed())[0].status, "applied");
    const ok = await call("PATCH", `/engine/apps/a1?updated_at=${encodeURIComponent(before)}`, { status: "interview" });
    assert.deepEqual(await ok.json(), { updated: true });
    const after = (await listed())[0];
    assert.equal(after.status, "interview");
    assert.notEqual(after.updated_at, before);
    // the old updated_at no longer matches
    const again = await call("PATCH", `/engine/apps/a1?updated_at=${encodeURIComponent(before)}`, { status: "offer" });
    assert.deepEqual(await again.json(), { updated: false });
  });

  it("deletes only while updated_at still matches", async () => {
    await call("POST", "/engine/apps", app);
    const current = (await listed())[0].updated_at as string;
    const stale = await call("DELETE", `/engine/apps/a1?updated_at=${encodeURIComponent("2000-01-01 00:00:00+00")}`);
    assert.deepEqual(await stale.json(), { deleted: false });
    assert.equal((await listed()).length, 1);
    const ok = await call("DELETE", `/engine/apps/a1?updated_at=${encodeURIComponent(current)}`);
    assert.deepEqual(await ok.json(), { deleted: true });
    assert.equal((await listed()).length, 0);
  });

  it("needs updated_at on PATCH and DELETE", async () => {
    assert.equal((await call("PATCH", "/engine/apps/a1", { status: "x" })).status, 400);
    assert.equal((await call("DELETE", "/engine/apps/a1")).status, 400);
  });

  it("never returns or changes another user's rows", async () => {
    await run("insert into jobkit_apps (user_id, id, company, role, applied) values ($1,'b1','Other','r','2026-01-01')", [U2]);
    assert.equal((await listed()).length, 0);
    const ts = String((await run("select updated_at::text as t from jobkit_apps where id = 'b1'", []))[0].t);
    const patch = await call("PATCH", `/engine/apps/b1?updated_at=${encodeURIComponent(ts)}`, { status: "offer" });
    assert.deepEqual(await patch.json(), { updated: false });
    const del = await call("DELETE", `/engine/apps/b1?updated_at=${encodeURIComponent(ts)}`);
    assert.deepEqual(await del.json(), { deleted: false });
    assert.deepEqual(await run("select user_id, status from jobkit_apps where id = 'b1'", []), [{ user_id: U2, status: "applied" }]);
  });

  it("upserts openings on user_id and external_key", async () => {
    const row = { external_key: "k1", company: "C", title: "T", url: "https://u" };
    const first = await call("POST", "/engine/openings", { rows: [row] });
    assert.deepEqual(await first.json(), { upserted: 1 });
    const second = await call("POST", "/engine/openings", { rows: [{ ...row, title: "T2" }] });
    assert.deepEqual(await second.json(), { upserted: 1 });
    assert.deepEqual(await run("select title, user_id from jobkit_external_openings", []), [{ title: "T2", user_id: U1 }]);
    assert.deepEqual(await (await call("POST", "/engine/openings", { rows: [] })).json(), { upserted: 0 });
  });

  it("answers 404 for unknown engine routes", async () => {
    assert.equal((await call("GET", "/engine/nothing")).status, 404);
  });
});
