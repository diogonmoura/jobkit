import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { requireUser } from "../auth";
import type { Env } from "../types";

const OWNER = "6f1c2a52-3a37-4f0e-9a3c-2a1f5f8f6a11";
const OTHER = "0d6e3b1a-1111-4a2b-8c3d-9e8f7a6b5c4d";

const env = {
  JOBKIT_DB: "postgres",
  DATABASE_URL: "postgres://u:p@db.example.test/neondb",
  NEON_AUTH_BASE_URL: "https://ep-x.neonauth.example.test/neondb/auth",
} as unknown as Env;

const request = () => new Request("https://jobs.example.test/api/preferences", { headers: { cookie: "s=1" } });
const signedIn = (userId: string) => async () => ({ userId });
const signedOut = async () => null;
const owner = (id: string | null) => async () => id;

describe("requireUser on Postgres", () => {
  it("returns a context for the owner", async () => {
    const ctx = await requireUser(request(), env, { sessionUser: signedIn(OWNER), ownerId: owner(OWNER) });
    assert.ok(ctx);
    assert.equal(ctx.userId, OWNER);
    assert.equal(typeof ctx.db.from, "function");
    assert.deepEqual(ctx.pending, []);
  });

  it("returns null for a signed-in non-owner", async () => {
    assert.equal(await requireUser(request(), env, { sessionUser: signedIn(OTHER), ownerId: owner(OWNER) }), null);
  });

  it("returns null without a session", async () => {
    assert.equal(await requireUser(request(), env, { sessionUser: signedOut, ownerId: owner(OWNER) }), null);
  });

  it("returns null before an owner exists", async () => {
    assert.equal(await requireUser(request(), env, { sessionUser: signedIn(OWNER), ownerId: owner(null) }), null);
  });
});
