import { applyCookies, createServiceSupabase, createSupabaseFromRequest, getOwnerId, supabaseConfigured } from "./auth";
import { handleApi } from "./api";
import { loadProfile } from "./preferences";
import { runJobScan } from "./scan/pipeline";
import type { Env } from "./types";

/** Served without a session: the setup wizard, the sign-in page and their assets. */
const PUBLIC_PATHS = new Set([
  "/favicon.ico",
  "/robots.txt",
  "/setup",
  "/setup.html",
  "/setup.js",
  "/setup.css",
  "/login",
  "/login.html",
  "/jobkit-tracker.css",
  "/jobkit-theme.js",
  "/setup/jobkit-schema.sql",
]);

function redirect(to: string, request: Request): Response {
  return Response.redirect(new URL(to, request.url).toString(), 302);
}

function configScript(env: Env): string {
  return `window.__JOBKIT_SUPABASE=${JSON.stringify({ url: env.SUPABASE_URL ?? "", anonKey: env.SUPABASE_ANON_KEY ?? "" })};`;
}

function injectConfig(html: string, env: Env): string {
  const snippet = `<script>${configScript(env)}</script>`;
  return html.includes("</head>") ? html.replace("</head>", `${snippet}</head>`) : snippet + html;
}

async function servePage(request: Request, env: Env, file: string): Promise<Response> {
  const url = new URL(request.url);
  const asset = await env.ASSETS.fetch(new Request(new URL(file, url.origin), request));
  if (!asset.ok) return asset;
  return new Response(injectConfig(await asset.text(), env), {
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export default {
  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    if (!supabaseConfigured(env)) return;
    ctx.waitUntil(
      (async () => {
        const owner = await getOwnerId(env);
        if (!owner) return;
        await runJobScan(env, createServiceSupabase(env), owner, "cron", null);
      })().catch(() => {}),
    );
  },

  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname.startsWith("/api/")) {
      const apiResponse = await handleApi(request, env, url);
      if (apiResponse) return apiResponse;
    }

    // The publishable (anon) key is public by design; the service role key never leaves the Worker.
    if (url.pathname === "/config.js") {
      return new Response(configScript(env), {
        headers: { "Content-Type": "application/javascript; charset=utf-8", "Cache-Control": "no-store" },
      });
    }
    if (url.pathname === "/setup" || url.pathname === "/setup.html") return servePage(request, env, "/setup");
    if (url.pathname === "/login" || url.pathname === "/login.html") {
      if (!supabaseConfigured(env)) return redirect("/setup", request);
      return servePage(request, env, "/login");
    }
    if (PUBLIC_PATHS.has(url.pathname) || url.pathname.startsWith("/vendor/")) return env.ASSETS.fetch(request);

    if (!supabaseConfigured(env)) return redirect("/setup", request);

    const owner = await getOwnerId(env);
    if (!owner) return redirect("/setup", request);

    const { supabase, pending } = createSupabaseFromRequest(request, env);
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user || user.id !== owner) {
      return applyCookies(redirect(`/login?next=${encodeURIComponent(url.pathname + url.search)}`, request), pending);
    }

    if (url.pathname === "/" || url.pathname === "/index.html") {
      const profile = await loadProfile(createServiceSupabase(env), user.id).catch(() => null);
      if (profile && !profile.onboarded_at) return applyCookies(redirect("/setup", request), pending);
      return applyCookies(await servePage(request, env, "/"), pending);
    }

    return applyCookies(await env.ASSETS.fetch(request), pending);
  },
};
