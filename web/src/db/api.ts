import { jsonResponse } from "../auth";
import type { Env } from "../types";
import { BROWSER_ACCESS } from "./browser-access";
import { neonRunner, runPlan, type SqlRunner } from "./postgres";
import type { QueryPlan } from "./types";

const OPS = ["select", "insert", "update", "upsert", "delete"];
const FILTER_OPS = ["eq", "in", "gte"];
// conflictOwner is internal and stripped, not rejected.
const KEYS = ["table", "op", "columns", "count", "head", "rows", "patch", "onConflict", "conflictOwner", "returning", "filters", "order", "limit", "cardinality"];

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const optString = (v: unknown) => v === undefined || typeof v === "string";

/** Checks the shape of a plan from the browser. Returns the plan, or a message saying what is wrong. */
export function parsePlan(body: unknown): QueryPlan | string {
  if (!isObject(body)) return "Body must be a JSON object";
  for (const k of Object.keys(body)) if (!KEYS.includes(k)) return `Unknown field: ${k}`;
  const { table, op, columns, count, head, rows, patch, onConflict, returning, filters, order, limit, cardinality } = body;
  if (typeof table !== "string") return "table must be a string";
  if (typeof op !== "string" || !OPS.includes(op)) return "op is not supported";
  if (!optString(columns) || !optString(onConflict) || !optString(returning)) return "columns, onConflict and returning must be strings";
  if (count !== undefined && count !== "exact") return "count must be exact";
  if (head !== undefined && typeof head !== "boolean") return "head must be a boolean";
  if (rows !== undefined && (!Array.isArray(rows) || !rows.every(isObject))) return "rows must be an array of objects";
  if (patch !== undefined && !isObject(patch)) return "patch must be an object";
  if (limit !== undefined && typeof limit !== "number") return "limit must be a number";
  if (cardinality !== undefined && cardinality !== "single" && cardinality !== "maybeSingle") return "cardinality is not supported";
  if (!Array.isArray(filters)) return "filters must be an array";
  for (const f of filters) {
    if (!isObject(f) || typeof f.column !== "string" || typeof f.op !== "string" || !FILTER_OPS.includes(f.op) || !("value" in f)) {
      return "filters must be {column, op, value} with op eq, in or gte";
    }
    if (f.op === "in" && !Array.isArray(f.value)) return "an in filter needs an array";
  }
  if (!Array.isArray(order)) return "order must be an array";
  for (const o of order) {
    if (!isObject(o) || typeof o.column !== "string" || typeof o.ascending !== "boolean" || (o.nullsFirst !== undefined && typeof o.nullsFirst !== "boolean")) {
      return "order must be {column, ascending, nullsFirst?}";
    }
  }
  return body as unknown as QueryPlan;
}

/** POST /api/db on Postgres setups: runs the browser's query plan as the owner, limited to BROWSER_ACCESS. */
export async function handleDbApi(request: Request, env: Env, userId: string, run: SqlRunner = neonRunner(env.DATABASE_URL as string)): Promise<Response> {
  if (!(request.headers.get("Content-Type") ?? "").toLowerCase().startsWith("application/json")) {
    return jsonResponse({ error: "Content-Type must be application/json" }, { status: 415 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: "Invalid JSON" }, { status: 400 });
  }
  const plan = parsePlan(body);
  if (typeof plan === "string") return jsonResponse({ error: plan }, { status: 400 });
  const result = await runPlan(run, { ownerId: userId, access: BROWSER_ACCESS }, plan);
  if (result.error?.code === "42501") return jsonResponse({ error: result.error.message }, { status: 403 });
  if (result.error?.code === "PGRST100") return jsonResponse({ error: result.error.message }, { status: 400 });
  return jsonResponse(result);
}
