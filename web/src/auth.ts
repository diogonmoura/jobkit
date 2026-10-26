import { createServerClient, parseCookieHeader } from "@supabase/ssr";
import { createClient } from "@supabase/supabase-js";
import { dbConfigured, serviceDb } from "./db";
import type { Env } from "./types";

type CookiePending = {
  name: string;
  value: string;
  options: Record<string, unknown>;
};

export type AuthContext = {
  userId: string;
  supabase: ReturnType<typeof createServerClient>;
  pending: CookiePending[];
};

export function createSupabaseFromRequest(request: Request, env: Env) {
  const secure = new URL(request.url).protocol === "https:";
  const domain = env.COOKIE_DOMAIN || undefined;
  let cookieJar = parseCookieHeader(request.headers.get("Cookie") ?? "");
  const pending: CookiePending[] = [];

  const supabase = createServerClient(env.SUPABASE_URL, env.SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return cookieJar;
      },
      setAll(cookiesToSet: { name: string; value: string; options?: Record<string, unknown> }[]) {
        for (const { name, value, options } of cookiesToSet) {
          cookieJar = cookieJar.filter((c) => c.name !== name);
          if (value) cookieJar.push({ name, value });
          pending.push({
            name,
            value,
            options: {
              ...options,
              ...(domain ? { domain } : {}),
              path: "/",
              sameSite: "lax",
              secure,
            },
          });
        }
      },
    },
    cookieOptions: {
      ...(domain ? { domain } : {}),
      path: "/",
      sameSite: "lax",
      secure,
    },
  });

  return { supabase, pending };
}

export function createServiceSupabase(env: Env) {
  if (!env.SUPABASE_SERVICE_ROLE_KEY) {
    throw new Error("SUPABASE_SERVICE_ROLE_KEY not configured");
  }
  return createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

export async function requireUser(
  request: Request,
  env: Env,
): Promise<AuthContext | null> {
  const { supabase, pending } = createSupabaseFromRequest(request, env);
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;
  const owner = await getOwnerId(env);
  if (owner && user.id !== owner) return null;
  return { userId: user.id, supabase, pending };
}

/** True when the Worker has what it needs to talk to Supabase (set in wrangler.jsonc vars + secrets). */
export function supabaseConfigured(env: Env): boolean {
  return Boolean(env.SUPABASE_URL && env.SUPABASE_ANON_KEY && env.SUPABASE_SERVICE_ROLE_KEY);
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
