import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { proxyNeonAuth } from "./proxy";
import type { Env } from "../types";

const BASE = "https://ep-x.neonauth.example.test/neondb/auth";
const env = { NEON_AUTH_BASE_URL: BASE } as Env;

/** Records the upstream request and answers with `reply`. */
function upstream(reply: () => Response) {
  const seen: { request?: Request; init?: RequestInit } = {};
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    seen.request = new Request(input, init);
    seen.init = init;
    return reply();
  }) as typeof fetch;
  return { seen, fetchImpl };
}

describe("proxyNeonAuth", () => {
  it("makes upstream cookies first-party", async () => {
    const headers = new Headers();
    headers.append("Set-Cookie", "__Secure-x=1; Domain=neon.tech; Path=/neondb/auth; HttpOnly; Secure");
    headers.append("Set-Cookie", "__Secure-y=2; Path=/neondb/auth; domain=.neon.tech; HttpOnly; Secure; SameSite=None");
    const { fetchImpl } = upstream(() => new Response("{}", { status: 200, headers }));
    const res = await proxyNeonAuth(new Request("https://jobs.example.test/api/auth/sign-in/email", { method: "POST" }), env, "/sign-in/email", fetchImpl);
    assert.deepEqual(res.headers.getSetCookie(), [
      "__Secure-x=1; Path=/; HttpOnly; Secure; SameSite=Lax",
      "__Secure-y=2; Path=/; HttpOnly; Secure; SameSite=Lax",
    ]);
  });

  it("forces SameSite=Lax whether Neon sent SameSite=None or nothing", async () => {
    const headers = new Headers();
    headers.append("Set-Cookie", "a=1; Path=/neondb/auth; SameSite=None; HttpOnly; Secure");
    headers.append("Set-Cookie", "b=2; HttpOnly; Secure");
    headers.append("Set-Cookie", "c=3; samesite=strict");
    const { fetchImpl } = upstream(() => new Response(null, { status: 200, headers }));
    const res = await proxyNeonAuth(new Request("https://jobs.example.test/api/auth/sign-in/email", { method: "POST" }), env, "/sign-in/email", fetchImpl);
    assert.deepEqual(res.headers.getSetCookie(), [
      "a=1; Path=/; HttpOnly; Secure; SameSite=Lax",
      "b=2; Path=/; HttpOnly; Secure; SameSite=Lax",
      "c=3; Path=/; SameSite=Lax",
    ]);
  });

  it("forwards each endpoint jobkit uses", async () => {
    for (const path of ["/sign-up/email", "/sign-in/email", "/sign-out", "/request-password-reset", "/reset-password"]) {
      const { seen, fetchImpl } = upstream(() => Response.json({}));
      await proxyNeonAuth(new Request(`https://jobs.example.test/api/auth${path}`, { method: "POST" }), env, path, fetchImpl);
      assert.equal(seen.request?.url, `${BASE}${path}`);
    }
  });

  it("answers 404 for any other endpoint without contacting Neon", async () => {
    for (const path of ["/admin/list-users", "", "/", "/sign-in/email/", "/sign-in", "/token", "/get-session", "/token/x", "/get-session/../admin/list-users"]) {
      const { seen, fetchImpl } = upstream(() => new Response("unreachable"));
      const res = await proxyNeonAuth(new Request("https://jobs.example.test/api/auth/admin/list-users"), env, path, fetchImpl);
      assert.equal(res.status, 404, path);
      assert.equal(res.headers.get("content-type")?.startsWith("application/json"), true);
      assert.equal(seen.request, undefined, path);
    }
  });

  it("passes status and body through", async () => {
    const { fetchImpl } = upstream(() => Response.json({ message: "Invalid email or password" }, { status: 401 }));
    const res = await proxyNeonAuth(new Request("https://jobs.example.test/api/auth/sign-in/email", { method: "POST" }), env, "/sign-in/email", fetchImpl);
    assert.equal(res.status, 401);
    assert.deepEqual(await res.json(), { message: "Invalid email or password" });
  });

  it("forwards method, body, query and headers, but not host", async () => {
    const { seen, fetchImpl } = upstream(() => new Response(null, { status: 204 }));
    const req = new Request("https://jobs.example.test/api/auth/sign-up/email?callbackURL=%2F", {
      method: "POST",
      headers: { "content-type": "application/json", cookie: "a=1", origin: "https://jobs.example.test", host: "jobs.example.test" },
      body: JSON.stringify({ email: "a@b.test", password: "pw" }),
    });
    await proxyNeonAuth(req, env, "/sign-up/email", fetchImpl);
    assert.ok(seen.request);
    assert.equal(seen.request.url, `${BASE}/sign-up/email?callbackURL=%2F`);
    assert.equal(seen.request.method, "POST");
    assert.equal(await seen.request.text(), JSON.stringify({ email: "a@b.test", password: "pw" }));
    assert.equal(seen.request.headers.get("cookie"), "a=1");
    assert.equal(seen.request.headers.get("origin"), "https://jobs.example.test");
    assert.equal(seen.request.headers.get("content-type"), "application/json");
    assert.equal(new Headers(seen.init?.headers).has("host"), false);
    assert.equal(seen.init?.redirect, "manual");
  });

  it("sends no body on GET", async () => {
    const { seen, fetchImpl } = upstream(() => Response.json(null));
    await proxyNeonAuth(new Request("https://jobs.example.test/api/auth/request-password-reset"), env, "/request-password-reset", fetchImpl);
    assert.equal(seen.request?.method, "GET");
    assert.equal(seen.init?.body, undefined);
  });

  it("answers 503 when Neon Auth is not configured", async () => {
    const { seen, fetchImpl } = upstream(() => new Response("unreachable"));
    const res = await proxyNeonAuth(new Request("https://jobs.example.test/api/auth/get-session"), {} as Env, "/sign-in/email", fetchImpl);
    assert.equal(res.status, 503);
    assert.equal(seen.request, undefined);
  });

  it("answers 502 when Neon Auth is unreachable", async () => {
    const boom = (async () => { throw new Error("down"); }) as typeof fetch;
    const res = await proxyNeonAuth(new Request("https://jobs.example.test/api/auth/get-session"), env, "/sign-in/email", boom);
    assert.equal(res.status, 502);
  });
});
