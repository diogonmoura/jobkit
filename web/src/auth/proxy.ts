import type { Env } from "../types";

/**
 * Makes a Neon Auth cookie first-party: drops Domain (so the browser scopes it to this Worker's host)
 * and replaces Path with "/" (so every page and /api call sends it). Other attributes stay as Neon set them.
 */
export function firstPartyCookie(setCookie: string): string {
  const [pair, ...attrs] = setCookie.split(";").map((p) => p.trim());
  const kept = attrs.filter((a) => {
    const key = a.split("=")[0].trim().toLowerCase();
    return a && key !== "domain" && key !== "path";
  });
  return [pair, "Path=/", ...kept].join("; ");
}

function errorResponse(status: number, error: string): Response {
  return Response.json({ error }, { status });
}

/**
 * Forwards /api/auth/<path> to <NEON_AUTH_BASE_URL>/<path> (Better Auth: sign-up/email, sign-in/email,
 * sign-out, get-session, token, ...) so the browser only ever talks to this Worker and the session
 * cookies are HttpOnly on our own host.
 */
export async function proxyNeonAuth(request: Request, env: Env, path: string, fetchImpl: typeof fetch = fetch): Promise<Response> {
  const base = env.NEON_AUTH_BASE_URL?.trim().replace(/\/+$/, "");
  if (!base) return errorResponse(503, "Neon Auth is not configured");

  const headers = new Headers(request.headers);
  headers.delete("host");
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetchImpl(`${base}${path}${new URL(request.url).search}`, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
    });
  } catch {
    return errorResponse(502, "Neon Auth is unreachable");
  }

  const out = new Headers(upstream.headers);
  out.delete("set-cookie");
  for (const c of upstream.headers.getSetCookie()) out.append("Set-Cookie", firstPartyCookie(c));
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}
