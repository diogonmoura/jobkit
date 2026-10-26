import { asDb, dbConfigured, dbMode, ownerDb, serviceDb } from "./db";
import { createSupabaseFromRequest, type CookiePending } from "./db/supabase";
import type { Db } from "./db/types";
import { neonSessionUser, type NeonUser } from "./auth/neon";
import type { Env } from "./types";

export type AuthContext = {
  userId: string;
  /** Supabase: the request's own client (row-level security). Postgres: a Db limited to the owner's rows. */
  db: Db;
  pending: CookiePending[];
};

/** Seams for tests; production uses the real Neon session check and owner lookup. */
export type AuthDeps = {
  sessionUser?: (request: Request, env: Env) => Promise<NeonUser | null>;
  ownerId?: (env: Env) => Promise<string | null>;
};

/**
 * Postgres: the signed-in Neon Auth user, but only when it is the owner. Null when signed out,
 * signed in as anyone else, or before an owner exists.
 */
export async function neonOwnerId(request: Request, env: Env, deps: AuthDeps = {}): Promise<string | null> {
  const user = await (deps.sessionUser ?? neonSessionUser)(request, env);
  if (!user) return null;
  const owner = await (deps.ownerId ?? getOwnerId)(env);
  return owner && user.userId === owner ? user.userId : null;
}

export async function requireUser(
  request: Request,
  env: Env,
  deps: AuthDeps = {},
): Promise<AuthContext | null> {
  if (dbMode(env) === "postgres") {
    const userId = await neonOwnerId(request, env, deps);
    return userId ? { userId, db: ownerDb(env, userId), pending: [] } : null;
  }
  const { supabase, pending } = createSupabaseFromRequest(request, env);
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;
  const owner = await getOwnerId(env);
  if (owner && user.id !== owner) return null;
  return { userId: user.id, db: asDb(supabase), pending };
}

/**
 * The single owner of this jobkit: JOBKIT_OWNER_USER_ID if set, otherwise the account claimed in the setup wizard
 * (row in jobkit_instance). Returns null before setup (or before migrations are applied).
 */
export async function getOwnerId(env: Env): Promise<string | null> {
  if (env.JOBKIT_OWNER_USER_ID) return env.JOBKIT_OWNER_USER_ID;
  if (!dbConfigured(env)) return null;
  const { data, error } = await serviceDb(env).from("jobkit_instance").select("owner_user_id").eq("id", 1).maybeSingle();
  if (error) return null;
  return data?.owner_user_id ? String(data.owner_user_id) : null;
}

/** Constant-time string comparison for secrets. */
export function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export function jsonResponse(
  body: unknown,
  init: ResponseInit = {},
  pending: CookiePending[] = [],
): Response {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json; charset=utf-8");
  if (init.status === undefined) {
    /* default 200 */
  }
  let response = new Response(JSON.stringify(body), { ...init, headers });
  if (pending.length) {
    response = applyCookies(response, pending);
  }
  return response;
}

type CookieSerializeOptions = {
  domain?: string;
  path?: string;
  maxAge?: number;
  expires?: Date;
  httpOnly?: boolean;
  secure?: boolean;
  sameSite?: "lax" | "strict" | "none";
};

function serializeCookie(
  name: string,
  value: string,
  options: CookieSerializeOptions,
): string {
  const parts = [`${name}=${encodeURIComponent(value)}`];
  if (options.domain) parts.push(`Domain=${options.domain}`);
  if (options.path) parts.push(`Path=${options.path ?? "/"}`);
  if (options.maxAge != null) parts.push(`Max-Age=${options.maxAge}`);
  if (options.expires) parts.push(`Expires=${options.expires.toUTCString()}`);
  if (options.httpOnly) parts.push("HttpOnly");
  if (options.secure) parts.push("Secure");
  if (options.sameSite) parts.push(`SameSite=${options.sameSite}`);
  return parts.join("; ");
}

export function applyCookies(
  response: Response,
  cookies: CookiePending[],
): Response {
  if (!cookies.length) return response;
  const headers = new Headers(response.headers);
  for (const { name, value, options } of cookies) {
    headers.append(
      "Set-Cookie",
      serializeCookie(name, value, options as CookieSerializeOptions),
    );
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}
