import { describe, it } from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error plain browser module without types
import { createJobkitDb } from "../public/jobkit-db.js";

const ROW = {
  id: "acme-pm-2026-09-01", company: "Acme", role: "PM", applied: "2026-09-01", cv: "cv_acme", status: "applied",
  last: "2026-09-01", posting: "", events: [], cv_md: "# CV", posting_text: "", updated_at: "2026-10-21T10:00:00.123+00:00",
};

/** A Supabase stand-in: every query-builder call chains, and awaiting it answers from `answer(op, table)`. */
function fakeSupabase(answer: (op: string, table: string) => unknown) {
  const calls: { table: string; op: string; payload?: unknown; filters: [string, unknown][] }[] = [];
  const builder = (table: string) => {
    const call = { table, op: "select", payload: undefined as unknown, filters: [] as [string, unknown][] };
    calls.push(call);
    const b: Record<string, unknown> = {};
    for (const m of ["select", "order", "limit", "maybeSingle", "single", "in", "neq", "is", "gte", "lte"]) b[m] = () => b;
    b.eq = (col: string, val: unknown) => { call.filters.push([col, val]); return b; };
    for (const op of ["update", "upsert", "insert", "delete"]) b[op] = (payload?: unknown) => { call.op = op; call.payload = payload; return b; };
    b.then = (resolve: (v: unknown) => void) => resolve(answer(call.op, table));
    return b;
  };
  const channel: Record<string, unknown> = {};
  channel.on = () => channel;
  channel.subscribe = () => channel;
  return { calls, from: builder, channel: () => channel, removeChannel: () => {} };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

describe("apps doc update", () => {
  it("sends only the changed fields, guarded by the row's updated_at", async () => {
    const sb = fakeSupabase((op, table) =>
      table === "jobkit_apps" && op === "select" ? { data: [ROW], error: null }
        : op === "update" ? { data: [{ id: ROW.id }], error: null } : { data: [], error: null });
    const db = createJobkitDb(sb, "u-1");
    await tick();
    await db.doc(`apps/${ROW.id}`).update({ status: "ack", last: "2026-09-02" });
    const upd = sb.calls.find((c) => c.op === "update" && c.table === "jobkit_apps")!;
    assert.deepEqual(upd.payload, { status: "ack", last: "2026-09-02" });
    assert.deepEqual(upd.filters, [["user_id", "u-1"], ["id", ROW.id], ["updated_at", ROW.updated_at]]);
    assert.equal(sb.calls.some((c) => c.op === "upsert" && c.table === "jobkit_apps"), false);
  });

  it("reloads and reports instead of overwriting when the row changed elsewhere", async () => {
    const sb = fakeSupabase((op, table) =>
      table === "jobkit_apps" && op === "select" ? { data: [ROW], error: null } : { data: [], error: null });
    const db = createJobkitDb(sb, "u-1");
    await tick();
    const selectsBefore = sb.calls.filter((c) => c.table === "jobkit_apps" && c.op === "select").length;
    await assert.rejects(db.doc(`apps/${ROW.id}`).update({ status: "ack" }), /changed elsewhere/);
    assert.ok(sb.calls.filter((c) => c.table === "jobkit_apps" && c.op === "select").length > selectsBefore);
  });
});
