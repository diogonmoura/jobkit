import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { PGlite } from "@electric-sql/pglite";
import { handleSetup, probeNeonAuth, type SetupDeps } from "./setup";
import { applyMigrations, createPostgresDb, pendingMigrations, TYPE_PARSERS, type SqlRunner } from "./db/postgres";

const U1 = "00000000-0000-0000-0000-000000000001";
const U2 = "00000000-0000-0000-0000-000000000002";
const PASSWORD = "s3cretpw";
const baseEnv = {
  JOBKIT_DB: "postgres",
  DATABASE_URL: `postgres://neondb_owner:${PASSWORD}@ep-cool-1234.eu-central-1.aws.neon.tech/neondb`,
  NEON_AUTH_BASE_URL: "https://ep-cool-1234.neonauth.eu-central-1.aws.neon.tech/neondb/auth",
} as any;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const trusted = async () => json({ message: "Invalid body" }, 400);
const untrusted = async () => json({ message: "Invalid origin", code: "INVALID_ORIGIN" }, 403);

describe("setup wizard on Postgres", () => {
  let run: SqlRunner;
  let signedIn: { userId: string; email?: string } | null;
  let deps: SetupDeps;

  beforeEach(() => {
    const pg = new PGlite({ parsers: TYPE_PARSERS });
    run = async (text, values) => (await pg.query(text, values as any[])).rows as Record<string, unknown>[];
    signedIn = null;
    deps = { run, db: createPostgresDb(run, {}), sessionUser: async () => signedIn, fetch: trusted as any };
  });

  const call = (method: string, path: string, body?: unknown, env: any = baseEnv) =>
    handleSetup(
      new Request("https://jobkit.example/api/setup" + path.slice(6), {
        method,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
      env,
      path,
      deps,
    ) as Promise<Response>;

  it("reports pending migrations, the host only, and a trusted origin", async () => {
    const res = await call("GET", "/setup/status");
    const text = await res.text();
    const body = JSON.parse(text);
    assert.equal(body.db.mode, "postgres");
    assert.equal(body.db.host, "ep-cool-1234.eu-central-1.aws.neon.tech");
    assert.ok(body.db.pendingMigrations.length > 0);
    assert.deepEqual(body.db.neonAuth, { reachable: true, originTrusted: true, origin: "https://jobkit.example" });
    assert.equal(body.owner, false);
    assert.equal(body.supabase.configured, true);
    assert.ok(!text.includes(PASSWORD));
    assert.ok(!text.includes("neondb_owner"));
    assert.ok(!text.includes("postgres://"));
  });

  it("reports originTrusted false when Neon Auth answers INVALID_ORIGIN", async () => {
    deps.fetch = untrusted as any;
    const body = (await (await call("GET", "/setup/status")).json()) as any;
    assert.deepEqual(body.db.neonAuth, { reachable: true, originTrusted: false, origin: "https://jobkit.example" });
  });

  it("probes with the request origin and an empty JSON body", async () => {
    let seen: { url: string; init: RequestInit } | null = null;
    await probeNeonAuth(baseEnv, "https://jobkit.example", (async (url: string, init: RequestInit) => {
      seen = { url, init };
      return json({}, 400);
    }) as any);
    assert.equal(seen!.url, `${baseEnv.NEON_AUTH_BASE_URL}/sign-in/email`);
    assert.equal(seen!.init.method, "POST");
    assert.equal((seen!.init.headers as any).Origin, "https://jobkit.example");
    assert.equal(seen!.init.body, "{}");
  });

  it("marks Neon Auth unreachable when the probe throws", async () => {
    deps.fetch = (async () => {
      throw new Error("down");
    }) as any;
    const body = (await (await call("GET", "/setup/status")).json()) as any;
    assert.equal(body.db.neonAuth.reachable, false);
  });

  it("prepare-db applies migrations while there is no owner, and the status then shows none pending", async () => {
    const res = await call("POST", "/setup/prepare-db");
    assert.equal(res.status, 200);
    assert.ok(((await res.json()) as any).applied.length > 0);
    assert.deepEqual(await pendingMigrations(run), []);
    const status = (await (await call("GET", "/setup/status")).json()) as any;
    assert.deepEqual(status.db.pendingMigrations, []);
  });

  it("prepare-db after an owner exists: 401 without a session, 403 for another user, ok for the owner", async () => {
    await applyMigrations(run);
    await deps.db!.from("jobkit_instance").insert({ id: 1, owner_user_id: U1 });
    assert.equal((await call("POST", "/setup/prepare-db")).status, 401);
    signedIn = { userId: U2 };
    assert.equal((await call("POST", "/setup/prepare-db")).status, 403);
    signedIn = { userId: U1 };
    const res = await call("POST", "/setup/prepare-db");
    assert.equal(res.status, 200);
    assert.deepEqual(((await res.json()) as any).applied, []);
  });

  it("claim needs a session, and refuses before the database is prepared", async () => {
    assert.equal((await call("POST", "/setup/claim", {})).status, 401);
    signedIn = { userId: U1 };
    assert.equal((await call("POST", "/setup/claim", {})).status, 412);
  });

  it("claim succeeds once; a second user gets 409", async () => {
    await applyMigrations(run);
    signedIn = { userId: U1, email: "me@example.com" };
    const first = await call("POST", "/setup/claim", {});
    assert.equal(first.status, 200);
    const { data } = await deps.db!.from("jobkit_instance").select("id, owner_user_id").maybeSingle();
    assert.deepEqual(data, { id: 1, owner_user_id: U1 });
    signedIn = { userId: U2 };
    assert.equal((await call("POST", "/setup/claim", {})).status, 409);
    signedIn = { userId: U1, email: "me@example.com" };
    assert.equal((await call("POST", "/setup/claim", {})).status, 200);
    const status = (await (await call("GET", "/setup/status")).json()) as any;
    assert.equal(status.owner, true);
    assert.equal(status.isOwner, true);
    assert.equal(status.email, "me@example.com");
  });

  it("with SETUP_TOKEN set, a wrong or missing token gets 403 and the right one claims", async () => {
    await applyMigrations(run);
    const env = { ...baseEnv, SETUP_TOKEN: "s" };
    signedIn = { userId: U1 };
    assert.equal((await call("POST", "/setup/claim", { token: "nope" }, env)).status, 403);
    assert.equal((await call("POST", "/setup/claim", {}, env)).status, 403);
    assert.equal((await call("POST", "/setup/claim", { token: "s" }, env)).status, 200);
    const status = (await (await call("GET", "/setup/status", undefined, env)).json()) as any;
    assert.equal(status.setupTokenRequired, false);
  });

  it("status asks for the setup token while there is no owner", async () => {
    await applyMigrations(run);
    const status = (await (await call("GET", "/setup/status", undefined, { ...baseEnv, SETUP_TOKEN: "s" })).json()) as any;
    assert.equal(status.setupTokenRequired, true);
  });

  it("claim by a second user says why in the error field", async () => {
    await applyMigrations(run);
    await deps.db!.from("jobkit_instance").insert({ id: 1, owner_user_id: U1 });
    signedIn = { userId: U2 };
    const res = await call("POST", "/setup/claim", {});
    assert.equal(res.status, 409);
    assert.equal(((await res.json()) as any).error, "This jobkit already has an owner");
  });

  it("prepare-db failure does not leak the driver message", async () => {
    deps.run = async () => {
      throw new Error("password authentication failed for user neondb_owner");
    };
    const real = console.error;
    console.error = () => {};
    try {
      const res = await call("POST", "/setup/prepare-db");
      const text = await res.text();
      assert.equal(res.status, 500);
      assert.ok(!text.includes("neondb_owner"));
      assert.equal(JSON.parse(text).error, "Could not prepare the database. Check DATABASE_URL.");
    } finally {
      console.error = real;
    }
  });

  it("status reports Neon Auth unreachable so the wizard can block the database step", async () => {
    deps.fetch = (async () => {
      throw new Error("down");
    }) as any;
    await applyMigrations(run);
    const body = (await (await call("GET", "/setup/status")).json()) as any;
    assert.deepEqual(body.db.pendingMigrations, []);
    assert.equal(body.db.neonAuth.reachable, false);
  });
});
