import { createServerClient, parseCookieHeader } from "@supabase/ssr";
import { createClient } from "@supabase/supabase-js";
import type { Env } from "../types";

export type CookiePending = {
  name: string;
  value: string;
  options: Record<string, unknown>;
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

/** True when the Worker has what it needs to talk to Supabase (set in wrangler.jsonc vars + secrets). */
export function supabaseConfigured(env: Env): boolean {
  return Boolean(env.SUPABASE_URL && env.SUPABASE_ANON_KEY && env.SUPABASE_SERVICE_ROLE_KEY);
}
