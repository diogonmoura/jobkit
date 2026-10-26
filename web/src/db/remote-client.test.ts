import { describe, it } from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error plain browser module without types
import { createRemoteClient } from "../../public/jobkit-remote-db.js";

type Sent = { url: string; init: RequestInit };

function fakeFetch(respond: (url: string) => { status?: number; body: unknown }) {
  const sent: Sent[] = [];
  const fetchImpl = async (url: string, init: RequestInit = {}) => {
    sent.push({ url, init });
    const { status = 200, body } = respond(url);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  };
  return { sent, fetchImpl };
}

describe("createRemoteClient", () => {
  it("sends an update chain as one QueryPlan and resolves to the response body", async () => {
    const { sent, fetchImpl } = fakeFetch(() => ({ body: { data: [{ id: "a" }], error: null } }));
    const client = createRemoteClient(fetchImpl);
    const result = await client.from("jobkit_apps").update({ status: "x" }).eq("id", "a").select("id");
    assert.deepEqual(result, { data: [{ id: "a" }], error: null });
    assert.equal(sent.length, 1);
    assert.equal(sent[0].url, "/api/db");
    assert.equal(sent[0].init.method, "POST");
    assert.equal(sent[0].init.credentials, "same-origin");
    assert.deepEqual(sent[0].init.headers, { "Content-Type": "application/json" });
    assert.deepEqual(JSON.parse(sent[0].init.body as string), {
      table: "jobkit_apps",
      op: "update",
      patch: { status: "x" },
      filters: [{ column: "id", op: "eq", value: "a" }],
      returning: "id",
      order: [],
    });
  });

  it("records selects, ordering, in, limit and cardinality", async () => {
    const { sent, fetchImpl } = fakeFetch(() => ({ body: { data: null, error: null } }));
    const client = createRemoteClient(fetchImpl);
    await client.from("jobkit_jobs").select("*").eq("user_id", "u").in("signal", ["up"]).order("created_at", { ascending: false }).limit(5).maybeSingle();
    await client.from("jobkit_apps").select("id", { count: "exact", head: true });
    await client.from("jobkit_profile").upsert({ a: 1 }, { onConflict: "user_id" });
    await client.from("jobkit_apps").delete().eq("id", "z");
    const plans = sent.map((s) => JSON.parse(s.init.body as string));
    assert.deepEqual(plans[0], {
      table: "jobkit_jobs", op: "select", columns: "*",
      filters: [{ column: "user_id", op: "eq", value: "u" }, { column: "signal", op: "in", value: ["up"] }],
      order: [{ column: "created_at", ascending: false }], limit: 5, cardinality: "maybeSingle",
    });
    assert.deepEqual(plans[1], { table: "jobkit_apps", op: "select", columns: "id", count: "exact", head: true, filters: [], order: [] });
    assert.deepEqual(plans[2], { table: "jobkit_profile", op: "upsert", rows: [{ a: 1 }], onConflict: "user_id", filters: [], order: [] });
    assert.deepEqual(plans[3], { table: "jobkit_apps", op: "delete", filters: [{ column: "id", op: "eq", value: "z" }], order: [] });
  });

  it("turns a rejected plan into a supabase-style error", async () => {
    const { fetchImpl } = fakeFetch(() => ({ status: 403, body: { error: "select on x is not allowed" } }));
    const client = createRemoteClient(fetchImpl);
    assert.deepEqual(await client.from("x").select("*"), { data: null, error: { message: "select on x is not allowed" } });
  });

  it("sends a 401 to the login page", async () => {
    const { fetchImpl } = fakeFetch(() => ({ status: 401, body: { error: "Unauthorized" } }));
    let to = "";
    const client = createRemoteClient(fetchImpl, (url: string) => { to = url; });
    const result = await client.from("x").select("*");
    assert.equal(result.error.message, "Unauthorized");
    assert.match(to, /^\/login\?next=/);
  });

  it("reads the session and signs out", async () => {
    const { sent, fetchImpl } = fakeFetch((url) => (url === "/api/session" ? { body: { userId: "u1" } } : { body: {} }));
    const client = createRemoteClient(fetchImpl);
    assert.deepEqual(await client.auth.getSession(), { data: { session: { user: { id: "u1" } } } });
    await client.auth.signOut();
    assert.equal(sent[1].url, "/api/auth/sign-out");
    assert.equal(sent[1].init.method, "POST");
    const signedOut = createRemoteClient(fakeFetch(() => ({ status: 401, body: { error: "Unauthorized" } })).fetchImpl);
    assert.deepEqual(await signedOut.auth.getSession(), { data: { session: null } });
  });
});
