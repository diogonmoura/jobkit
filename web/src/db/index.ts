import type { SupabaseClient } from "@supabase/supabase-js";
import { createServiceSupabase, supabaseConfigured } from "../auth";
import type { Env } from "../types";
import { createPostgresDb, neonRunner } from "./postgres";
import type { Db } from "./types";

export type DbMode = "supabase" | "postgres";

/** "postgres" only when JOBKIT_DB says so; anything else is the Supabase install that exists today. */
export function dbMode(env: Env): DbMode {
  return env.JOBKIT_DB === "postgres" ? "postgres" : "supabase";
}

export function dbConfigured(env: Env): boolean {
  if (dbMode(env) === "postgres") return Boolean(env.DATABASE_URL && env.NEON_AUTH_BASE_URL);
  return supabaseConfigured(env);
}

/**
 * The one place a supabase-js client becomes a Db. The client's query builder is a superset of the
 * Db surface (the app only uses what Db lists), but TypeScript cannot see that, so the cast lives here.
 */
export function asDb(client: SupabaseClient): Db {
  return client as unknown as Db;
}

/** Unscoped access for the Worker itself (cron, setup, owner lookup). Never hand this to browser input. */
export function serviceDb(env: Env): Db {
  if (dbMode(env) === "postgres") return createPostgresDb(neonRunner(env.DATABASE_URL as string), {});
  return asDb(createServiceSupabase(env));
}

/** True when serviceDb(env) can be built: the service key on Supabase, the connection string on Postgres. */
export function serviceDbAvailable(env: Env): boolean {
  return dbMode(env) === "postgres" ? Boolean(env.DATABASE_URL) : Boolean(env.SUPABASE_SERVICE_ROLE_KEY);
}

/** The service Db when there is one, otherwise the caller's own Db. */
export function serviceDbOr(env: Env, fallback: Db): Db {
  return serviceDbAvailable(env) ? serviceDb(env) : fallback;
}

/**
 * A Db limited to one owner's rows. Postgres only: on Supabase, request-scoped work keeps the
 * request's own client (row-level security protects the rows), and a service client scoped by
 * convention would bypass it.
 */
export function ownerDb(env: Env, ownerId: string): Db {
  if (dbMode(env) !== "postgres") {
    throw new Error("ownerDb is only available when JOBKIT_DB=postgres; on Supabase use the request's client");
  }
  return createPostgresDb(neonRunner(env.DATABASE_URL as string), { ownerId });
}
