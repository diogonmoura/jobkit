import { getOwnerId, jsonResponse, safeEqual } from "./auth";
import { serviceDb } from "./db";
import { TABLES } from "./db/tables";
import type { Db } from "./db/types";
import type { Env } from "./types";

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** The body's fields, or a message naming the first one that is not a column of the table. user_id is dropped: the owner is forced. */
function allowedFields(table: string, body: Record<string, unknown>, exclude: string[] = []): Record<string, unknown> | string {
  const columns = TABLES[table].columns;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(body)) {
    if (key === "user_id") continue;
    if (!columns.includes(key) || exclude.includes(key)) return `Unknown field: ${key}`;
    out[key] = value;
  }
  return out;
}

async function readObject(request: Request): Promise<Record<string, unknown> | null> {
  const body = await request.json().catch(() => null);
  return isObject(body) ? body : null;
}

const bad = (error: string) => jsonResponse({ error }, { status: 400 });
const failed = (error: { message: string; code?: string }) =>
  jsonResponse({ error: error.message }, { status: error.code === "23505" ? 409 : 500 });

/**
 * /api/engine/*: what the Python engine's `sync web` needs, behind JOBKIT_ENGINE_TOKEN. Works on both
 * database setups through serviceDb (unscoped), so every query here names the owner itself.
 * Returns null for paths that are not the engine's. `db` is a seam for tests.
 */
export async function handleEngineApi(request: Request, env: Env, path: string, db?: Db): Promise<Response | null> {
  if (path !== "/engine" && !path.startsWith("/engine/")) return null;

  const token = (request.headers.get("Authorization") ?? "").replace(/^Bearer /, "");
  if (!env.JOBKIT_ENGINE_TOKEN || !token || !safeEqual(token, env.JOBKIT_ENGINE_TOKEN)) {
    return jsonResponse({ error: "Unauthorized" }, { status: 401 });
  }

  const ownerId = await getOwnerId(env);
  if (!ownerId) return jsonResponse({ error: "No owner yet; finish /setup in the web app first" }, { status: 404 });
  const method = request.method;
  const route = path.slice("/engine".length);

  if (route === "/owner" && method === "GET") return jsonResponse({ ownerId });

  const store = db ?? serviceDb(env);

  if (route === "/apps" && method === "GET") {
    const { data, error } = await store.from("jobkit_apps").select("*").eq("user_id", ownerId);
    if (error) return failed(error);
    return jsonResponse({ rows: data ?? [] });
  }

  if (route === "/apps" && method === "POST") {
    const body = await readObject(request);
    if (!body) return bad("Body must be a JSON object");
    const fields = allowedFields("jobkit_apps", body);
    if (typeof fields === "string") return bad(fields);
    if (typeof fields.id !== "string" || !fields.id) return bad("id is required");
    const { error } = await store.from("jobkit_apps").insert({ ...fields, user_id: ownerId });
    if (error) return failed(error);
    return jsonResponse({ inserted: true }, { status: 201 });
  }

  const one = route.match(/^\/apps\/([^/]+)$/);
  if (one && (method === "PATCH" || method === "DELETE")) {
    let id: string;
    try {
      id = decodeURIComponent(one[1]);
    } catch {
      return bad("Invalid id");
    }
    const updatedAt = new URL(request.url).searchParams.get("updated_at");
    if (updatedAt === null) return bad("updated_at is required");
    let patch: Record<string, unknown> | undefined;
    if (method === "PATCH") {
      const body = await readObject(request);
      if (!body) return bad("Body must be a JSON object");
      const fields = allowedFields("jobkit_apps", body, ["id", "updated_at"]);
      if (typeof fields === "string") return bad(fields);
      patch = fields;
    }
    // A blank updated_at (a row the engine never saw a timestamp for) matches nothing.
    if (!updatedAt) return jsonResponse(method === "PATCH" ? { updated: false } : { deleted: false });
    const target = store.from("jobkit_apps");
    const query = patch ? target.update(patch) : target.delete();
    const { data, error } = await query.eq("user_id", ownerId).eq("id", id).eq("updated_at", updatedAt).select("id");
    if (error) return failed(error);
    const matched = (data ?? []).length > 0;
    return jsonResponse(patch ? { updated: matched } : { deleted: matched });
  }

  if (route === "/openings" && method === "POST") {
    const body = await readObject(request);
    if (!body || !Array.isArray(body.rows) || !body.rows.every(isObject)) return bad("rows must be an array of objects");
    const rows: Record<string, unknown>[] = [];
    for (const row of body.rows as Record<string, unknown>[]) {
      const fields = allowedFields("jobkit_external_openings", row);
      if (typeof fields === "string") return bad(fields);
      rows.push({ ...fields, user_id: ownerId });
    }
    if (!rows.length) return jsonResponse({ upserted: 0 });
    const { error } = await store.from("jobkit_external_openings").upsert(rows, { onConflict: "user_id,external_key" });
    if (error) return failed(error);
    return jsonResponse({ upserted: rows.length });
  }

  return jsonResponse({ error: "Not found" }, { status: 404 });
}
