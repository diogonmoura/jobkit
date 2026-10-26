import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "./index";
import type { Env } from "./types";

const pg = {
  JOBKIT_DB: "postgres",
  DATABASE_URL: "postgres://u:p@db.example.test/neondb",
  NEON_AUTH_BASE_URL: "https://ep-x.neonauth.example.test/neondb/auth",
  SUPABASE_URL: "https://abc.supabase.co",
  SUPABASE_ANON_KEY: "anon",
} as unknown as Env;

async function configJs(env: Env): Promise<string> {
  const res = await worker.fetch(new Request("https://jobs.example.test/config.js"), env);
  return res.text();
}

test("config.js tells the browser which database mode is active", async () => {
  const sb = await configJs({ SUPABASE_URL: "https://abc.supabase.co", SUPABASE_ANON_KEY: "anon" } as unknown as Env);
  assert.match(sb, /window\.__JOBKIT_DB=\{"mode":"supabase"\};/);
  assert.match(sb, /window\.__JOBKIT_SUPABASE=\{"url":"https:\/\/abc\.supabase\.co","anonKey":"anon"/);
  const p = await configJs(pg);
  assert.match(p, /window\.__JOBKIT_DB=\{"mode":"postgres"\};/);
  assert.match(p, /window\.__JOBKIT_SUPABASE=null;/);
  assert.doesNotMatch(p, /postgres:\/\//);
});

test("/api/auth/* goes to Neon Auth on Postgres", async () => {
  const real = globalThis.fetch;
  const seen: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    seen.push(String(input));
    return Response.json({ session: null });
  }) as typeof fetch;
  try {
    const res = await worker.fetch(new Request("https://jobs.example.test/api/auth/get-session?x=1"), pg);
    assert.equal(res.status, 200);
  } finally {
    globalThis.fetch = real;
  }
  assert.deepEqual(seen, ["https://ep-x.neonauth.example.test/neondb/auth/get-session?x=1"]);
});

test("the Postgres wizard's remote client is served without a session", async () => {
  const env = { ...pg, ASSETS: { fetch: async () => new Response("export const x = 1;") } } as unknown as Env;
  const res = await worker.fetch(new Request("https://jobs.example.test/jobkit-remote-db.js"), env);
  assert.equal(res.status, 200);
  assert.match(await res.text(), /export/);
});
