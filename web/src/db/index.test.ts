import { test } from "node:test";
import assert from "node:assert/strict";
import { dbConfigured, dbMode, ownerDb } from "./index";
import type { Env } from "../types";

const env = (vars: Record<string, string>) => vars as unknown as Env;

test("dbMode is supabase unless JOBKIT_DB is postgres", () => {
  assert.equal(dbMode(env({})), "supabase");
  assert.equal(dbMode(env({ JOBKIT_DB: "supabase" })), "supabase");
  assert.equal(dbMode(env({ JOBKIT_DB: "postgres" })), "postgres");
  assert.equal(dbMode(env({ JOBKIT_DB: "mysql" })), "supabase");
});

test("dbConfigured checks the variables of the active mode", () => {
  const sb = { SUPABASE_URL: "u", SUPABASE_ANON_KEY: "a", SUPABASE_SERVICE_ROLE_KEY: "s" };
  assert.equal(dbConfigured(env({})), false);
  assert.equal(dbConfigured(env(sb)), true);
  assert.equal(dbConfigured(env({ SUPABASE_URL: "u", SUPABASE_ANON_KEY: "a" })), false);
  const pg = { JOBKIT_DB: "postgres", DATABASE_URL: "postgres://x", NEON_AUTH_BASE_URL: "https://auth.example" };
  assert.equal(dbConfigured(env(pg)), true);
  assert.equal(dbConfigured(env({ ...pg, NEON_AUTH_BASE_URL: "" })), false);
  assert.equal(dbConfigured(env({ ...pg, DATABASE_URL: "" })), false);
  // Postgres mode ignores Supabase variables.
  assert.equal(dbConfigured(env({ ...sb, JOBKIT_DB: "postgres" })), false);
});

test("ownerDb is Postgres-only", () => {
  assert.throws(() => ownerDb(env({}), "u1"), /postgres/i);
  assert.ok(ownerDb(env({ JOBKIT_DB: "postgres", DATABASE_URL: "postgres://u:p@h/db" }), "u1").from);
});
