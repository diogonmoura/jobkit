import { applyCookies, getOwnerId, neonOwnerId } from "./auth";
import { dbConfigured, dbMode, serviceDb } from "./db";
import { createSupabaseFromRequest, type CookiePending } from "./db/supabase";
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

export function externalAuth(env: Env): boolean {
  return env.AUTH_MODE === "external" && Boolean(env.LOGIN_URL);
}

function loginRedirect(request: Request, env: Env): string {
  const url = new URL(request.url);
  if (externalAuth(env)) {
    const login = new URL(env.LOGIN_URL as string);
    login.searchParams.set("next", url.toString());
    return login.toString();
  }
  return `/login?next=${encodeURIComponent(url.pathname + url.search)}`;
}

/** Only a logo.dev publishable key (pk_…) may reach the browser; a secret key (sk_…) is ignored. */
export function publishableLogoToken(env: Env): string {
  const t = String(env.LOGO_DEV_TOKEN ?? "").trim();
  return t.startsWith("pk_") ? t : "";
}

function configScript(env: Env): string {
  const auth = {
    external: externalAuth(env),
    loginUrl: externalAuth(env) ? env.LOGIN_URL : "/login",
    logoutUrl: env.LOGOUT_URL || "",
    settingsUrl: env.SETTINGS_URL || "",
    homeUrl: env.HOME_URL || "",
    homeLabel: env.HOME_LABEL || "",
    cookieDomain: env.COOKIE_DOMAIN || "",
  };
  const mode = dbMode(env);
  // On Postgres the browser signs in through /api/auth, so it gets no Supabase client config.
  const supabase = mode === "postgres" ? null : { url: env.SUPABASE_URL ?? "", anonKey: env.SUPABASE_ANON_KEY ?? "", auth };
  return (
    `window.__JOBKIT_DB=${JSON.stringify({ mode })};` +
    `window.__JOBKIT_SUPABASE=${JSON.stringify(supabase)};` +
    // The page links the Supabase block carries on setup 1; none of these are secrets.
    (mode === "postgres" ? `window.__JOBKIT_NAV=${JSON.stringify({ logoutUrl: auth.logoutUrl, settingsUrl: auth.settingsUrl, homeUrl: auth.homeUrl, homeLabel: auth.homeLabel })};` : "") +
    `window.__JOBKIT_LANG=${JSON.stringify(env.JOBKIT_LANG || "en")};` +
    `window.__JOBKIT_LOGO_TOKEN=${JSON.stringify(publishableLogoToken(env))};`
  );
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
    if (!dbConfigured(env)) return;
    ctx.waitUntil(
      (async () => {
        const owner = await getOwnerId(env);
        if (!owner) return;
        await runJobScan(env, serviceDb(env), owner, "cron", null);
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
      if (!dbConfigured(env)) return redirect("/setup", request);
      // Sign-in happens in the other app; "/" sends signed-out visitors there.
      if (externalAuth(env)) return redirect("/", request);
      return servePage(request, env, "/login");
    }
    if (PUBLIC_PATHS.has(url.pathname) || url.pathname.startsWith("/vendor/")) return env.ASSETS.fetch(request);

    if (!dbConfigured(env)) return redirect("/setup", request);

    const owner = await getOwnerId(env);
    if (!owner) return redirect("/setup", request);

    let userId: string | null;
    let pending: CookiePending[] = [];
    if (dbMode(env) === "postgres") {
      userId = await neonOwnerId(request, env, { ownerId: async () => owner });
    } else {
      const client = createSupabaseFromRequest(request, env);
      pending = client.pending;
      const {
        data: { user },
      } = await client.supabase.auth.getUser();
      userId = user && user.id === owner ? user.id : null;
    }
    if (!userId) {
      return applyCookies(redirect(loginRedirect(request, env), request), pending);
    }

    if (url.pathname === "/" || url.pathname === "/index.html") {
      const profile = await loadProfile(serviceDb(env), userId).catch(() => null);
      // Only a fresh profile goes to the wizard; an instance set up before the wizard existed keeps working.
      if (profile && !profile.onboarded_at && !profile.roles.length) return applyCookies(redirect("/setup", request), pending);
      return applyCookies(await servePage(request, env, "/"), pending);
    }

    return applyCookies(await env.ASSETS.fetch(request), pending);
  },
};
