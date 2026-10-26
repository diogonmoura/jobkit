import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { SignJWT, exportJWK, generateKeyPair, type CryptoKey } from "jose";
import { neonSessionUser, verifyNeonToken } from "./neon";
import type { Env } from "../types";

const USER = "6f1c2a52-3a37-4f0e-9a3c-2a1f5f8f6a11";
let n = 0;

/** A fresh base URL per test, so the module-level JWKS cache never leaks keys between tests. */
function freshEnv(): { env: Env; origin: string } {
  const origin = `https://ep-test-${++n}.neonauth.example.test`;
  return { env: { NEON_AUTH_BASE_URL: `${origin}/neondb/auth` } as Env, origin };
}

async function keyPair() {
  const { publicKey, privateKey } = await generateKeyPair("EdDSA", { extractable: true });
  const jwk = { ...(await exportJWK(publicKey)), kid: "k1", alg: "EdDSA", use: "sig" };
  return { privateKey, jwks: { keys: [jwk] } };
}

function sign(key: CryptoKey | Uint8Array, alg: string, issuer: string, opts: { exp?: string | null; email?: string } = {}) {
  const jwt = new SignJWT(opts.email ? { email: opts.email } : {})
    .setProtectedHeader({ alg, kid: "k1" })
    .setSubject(USER)
    .setIssuer(issuer)
    .setIssuedAt();
  // exp: null signs a token with no expiry at all.
  if (opts.exp !== null) jwt.setExpirationTime(opts.exp ?? "15m");
  return jwt.sign(key);
}

function fakeFetch(jwks: unknown, token?: string): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url.endsWith("/.well-known/jwks.json")) return Response.json(jwks);
    if (url.endsWith("/token")) {
      return token
        ? Response.json({ token })
        : Response.json({ message: "Unauthorized", code: "UNAUTHORIZED" }, { status: 401 });
    }
    return new Response("not found", { status: 404 });
  }) as typeof fetch;
}

describe("verifyNeonToken", () => {
  it("accepts a valid token", async () => {
    const { env, origin } = freshEnv();
    const { privateKey, jwks } = await keyPair();
    const token = await sign(privateKey, "EdDSA", origin, { email: "a@b.test" });
    assert.deepEqual(await verifyNeonToken(token, env, fakeFetch(jwks)), { userId: USER, email: "a@b.test" });
  });

  it("omits email when the token has none", async () => {
    const { env, origin } = freshEnv();
    const { privateKey, jwks } = await keyPair();
    const token = await sign(privateKey, "EdDSA", origin);
    assert.deepEqual(await verifyNeonToken(token, env, fakeFetch(jwks)), { userId: USER });
  });

  it("rejects an expired token", async () => {
    const { env, origin } = freshEnv();
    const { privateKey, jwks } = await keyPair();
    const token = await sign(privateKey, "EdDSA", origin, { exp: "-1m" });
    assert.equal(await verifyNeonToken(token, env, fakeFetch(jwks)), null);
  });

  it("rejects a validly signed token without exp", async () => {
    const { env, origin } = freshEnv();
    const { privateKey, jwks } = await keyPair();
    const token = await sign(privateKey, "EdDSA", origin, { exp: null });
    assert.equal(await verifyNeonToken(token, env, fakeFetch(jwks)), null);
  });

  it("rejects the wrong issuer", async () => {
    const { env } = freshEnv();
    const { privateKey, jwks } = await keyPair();
    const token = await sign(privateKey, "EdDSA", "https://evil.example.test");
    assert.equal(await verifyNeonToken(token, env, fakeFetch(jwks)), null);
  });

  it("rejects an HS256 token", async () => {
    const { env, origin } = freshEnv();
    const { jwks } = await keyPair();
    const token = await sign(new TextEncoder().encode("0123456789abcdef0123456789abcdef"), "HS256", origin);
    assert.equal(await verifyNeonToken(token, env, fakeFetch(jwks)), null);
  });

  it("rejects a token signed by another key", async () => {
    const { env, origin } = freshEnv();
    const { jwks } = await keyPair();
    const other = await keyPair();
    const token = await sign(other.privateKey, "EdDSA", origin);
    assert.equal(await verifyNeonToken(token, env, fakeFetch(jwks)), null);
  });

  it("returns null when Neon Auth is not configured", async () => {
    assert.equal(await verifyNeonToken("x.y.z", {} as Env, fakeFetch({ keys: [] })), null);
  });
});

describe("neonSessionUser", () => {
  const req = () => new Request("https://jobs.example.test/api/anything", { headers: { cookie: "session=abc" } });

  it("exchanges the session cookie for a verified user", async () => {
    const { env, origin } = freshEnv();
    const { privateKey, jwks } = await keyPair();
    const token = await sign(privateKey, "EdDSA", origin);
    const seen: Request[] = [];
    const inner = fakeFetch(jwks, token);
    const spy = (async (input: RequestInfo | URL, init?: RequestInit) => {
      seen.push(new Request(input, init));
      return inner(input, init);
    }) as typeof fetch;
    assert.deepEqual(await neonSessionUser(req(), env, spy), { userId: USER });
    const call = seen.find((r) => r.url === `${env.NEON_AUTH_BASE_URL}/token`);
    assert.ok(call);
    assert.equal(call.headers.get("cookie"), "session=abc");
    assert.equal(call.headers.get("origin"), "https://jobs.example.test");
  });

  it("gives up on /token after 5 seconds", async () => {
    const { env, origin } = freshEnv();
    const { privateKey, jwks } = await keyPair();
    const token = await sign(privateKey, "EdDSA", origin);
    // jose's JWKS fetch makes its own timeout signal, so map each signal to its delay.
    const delays = new Map<AbortSignal, number>();
    const real = AbortSignal.timeout;
    AbortSignal.timeout = (ms: number) => {
      const s = real.call(AbortSignal, ms);
      delays.set(s, ms);
      return s;
    };
    let signal: AbortSignal | null | undefined;
    const inner = fakeFetch(jwks, token);
    const spy = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/token")) signal = init?.signal;
      return inner(input, init);
    }) as typeof fetch;
    try {
      await neonSessionUser(req(), env, spy);
    } finally {
      AbortSignal.timeout = real;
    }
    assert.ok(signal instanceof AbortSignal);
    assert.equal(delays.get(signal), 5000);
  });

  it("returns null without calling Neon when there is no Cookie header", async () => {
    const { env } = freshEnv();
    let calls = 0;
    const counting = (async () => {
      calls++;
      return new Response("", { status: 500 });
    }) as typeof fetch;
    assert.equal(await neonSessionUser(new Request("https://jobs.example.test/api/x"), env, counting), null);
    assert.equal(calls, 0);
  });

  it("returns null when /token answers 401", async () => {
    const { env } = freshEnv();
    const { jwks } = await keyPair();
    assert.equal(await neonSessionUser(req(), env, fakeFetch(jwks)), null);
  });

  it("returns null when the network fails", async () => {
    const { env } = freshEnv();
    const boom = (async () => { throw new Error("down"); }) as typeof fetch;
    assert.equal(await neonSessionUser(req(), env, boom), null);
  });

  it("returns null when Neon Auth is not configured", async () => {
    assert.equal(await neonSessionUser(req(), {} as Env, fakeFetch({ keys: [] })), null);
  });
});
