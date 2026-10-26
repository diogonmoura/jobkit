import { createRemoteJWKSet, customFetch, jwtVerify } from "jose";
import type { Env } from "../types";

export interface NeonUser {
  userId: string;
  email?: string;
}

type Jwks = ReturnType<typeof createRemoteJWKSet>;

// One JWKS getter per base URL, so keys are fetched and cached across requests in an isolate.
const jwksByBase = new Map<string, Jwks>();

function jwksFor(base: string, fetchImpl: typeof fetch): Jwks {
  let jwks = jwksByBase.get(base);
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(`${base}/.well-known/jwks.json`), {
      [customFetch]: (url, options) => fetchImpl(url, options),
    });
    jwksByBase.set(base, jwks);
  }
  return jwks;
}

function baseUrl(env: Env): string | null {
  const base = env.NEON_AUTH_BASE_URL?.trim().replace(/\/+$/, "");
  return base || null;
}

/** Verifies a Neon Auth JWT (EdDSA, issuer = origin of the base URL). Null on any failure. */
export async function verifyNeonToken(token: string, env: Env, fetchImpl: typeof fetch = fetch): Promise<NeonUser | null> {
  try {
    const base = baseUrl(env);
    if (!base) return null;
    const { payload } = await jwtVerify(token, jwksFor(base, fetchImpl), {
      algorithms: ["EdDSA"],
      issuer: new URL(base).origin,
    });
    if (!payload.sub) return null;
    const email = typeof payload.email === "string" ? payload.email : undefined;
    return email ? { userId: payload.sub, email } : { userId: payload.sub };
  } catch {
    return null;
  }
}

/** Trades the browser's Neon Auth session cookie for a token and verifies it. Null if signed out or on any failure. */
export async function neonSessionUser(request: Request, env: Env, fetchImpl: typeof fetch = fetch): Promise<NeonUser | null> {
  try {
    const base = baseUrl(env);
    if (!base) return null;
    const res = await fetchImpl(`${base}/token`, {
      headers: {
        cookie: request.headers.get("cookie") ?? "",
        // Neon Auth checks Origin against its trusted domains.
        origin: new URL(request.url).origin,
      },
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { token?: unknown };
    if (typeof body.token !== "string") return null;
    return await verifyNeonToken(body.token, env, fetchImpl);
  } catch {
    return null;
  }
}
