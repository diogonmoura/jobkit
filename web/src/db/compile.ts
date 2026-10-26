import type { QueryPlan, TableSpec } from "./types";

export class DbCompileError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DbCompileError";
  }
}

type Compiled = { text: string; values: unknown[] };

// Turns a recorded query plan into parameterized Postgres SQL. Table and column names
// come only from the allowlist (quoted), values only travel in $n parameters.
export function compile(plan: QueryPlan, tables: Record<string, TableSpec>): Compiled {
  const spec = Object.hasOwn(tables, plan.table) ? tables[plan.table] : undefined;
  if (!spec) throw new DbCompileError(`Unknown table: ${plan.table}`);
  const table = quote(plan.table);
  const values: unknown[] = [];
  const param = (v: unknown) => `$${values.push(v)}`;
  const col = (name: string) => {
    if (!spec.columns.includes(name)) throw new DbCompileError(`Unknown column ${name} on ${plan.table}`);
    return quote(name);
  };
  const colList = (list: string | undefined) =>
    !list || list.trim() === "*" ? "*" : list.split(",").map((c) => col(c.trim())).join(", ");
  // JSON columns travel as a JSON string with a jsonb cast; null stays SQL null.
  const value = (name: string, v: unknown) =>
    spec.jsonColumns.includes(name) && v !== null && v !== undefined ? `${param(JSON.stringify(v))}::jsonb` : param(v ?? null);
  const where = () => {
    if (!plan.filters.length) return "";
    const parts = plan.filters.map((f) => {
      const c = col(f.column);
      if (f.op === "eq") return `${c} = ${param(f.value)}`;
      if (f.op === "gte") return `${c} >= ${param(f.value)}`;
      if (!Array.isArray(f.value)) throw new DbCompileError(`in filter on ${f.column} needs an array`);
      return `${c} = any(${param(f.value)})`;
    });
    return ` where ${parts.join(" and ")}`;
  };
  const returning = () => (plan.returning ? ` returning ${colList(plan.returning)}` : "");

  if (plan.op === "select") {
    let text: string;
    if (plan.count || plan.head) {
      if (plan.count !== "exact" || !plan.head) throw new DbCompileError("count is only supported together with head");
      text = `select count(*)::int as count from ${table}${where()}`;
    } else {
      text = `select ${colList(plan.columns)} from ${table}${where()}`;
      if (plan.order.length) {
        text += " order by " + plan.order.map((o) =>
          `${col(o.column)} ${o.ascending ? "asc" : "desc"}${o.nullsFirst === undefined ? "" : o.nullsFirst ? " nulls first" : " nulls last"}`,
        ).join(", ");
      }
      if (plan.limit !== undefined) {
        if (!Number.isSafeInteger(plan.limit) || plan.limit < 0) throw new DbCompileError("limit must be a non-negative integer");
        text += ` limit ${plan.limit}`;
      }
    }
    return { text, values };
  }

  if (plan.op === "insert" || plan.op === "upsert") {
    const rows = plan.rows ?? [];
    if (!rows.length) throw new DbCompileError("insert needs at least one row");
    const keys: string[] = [];
    for (const row of rows) for (const k of Object.keys(row)) if (!keys.includes(k)) keys.push(k);
    const cols = keys.map(col);
    const tuples = rows.map((row) =>
      `(${keys.map((k) => (Object.hasOwn(row, k) && row[k] !== undefined ? value(k, row[k]) : "default")).join(", ")})`,
    );
    let text = `insert into ${table} (${cols.join(", ")}) values ${tuples.join(", ")}`;
    if (plan.op === "upsert") {
      const conflict = plan.onConflict ? plan.onConflict.split(",").map((c) => c.trim()) : spec.primaryKey;
      if (!conflict.length) throw new DbCompileError(`upsert on ${plan.table} needs onConflict (no primary key)`);
      const target = conflict.map(col).join(", ");
      const sets = keys.filter((k) => !conflict.includes(k)).map((k) => `${quote(k)} = excluded.${quote(k)}`);
      const ownerOnly = plan.conflictOwner !== undefined ? ` where ${table}.${quote("user_id")} = ${param(plan.conflictOwner)}` : "";
      text += ` on conflict (${target}) ` + (sets.length ? `do update set ${sets.join(", ")}${ownerOnly}` : "do nothing");
    }
    return { text: text + returning(), values };
  }

  if (plan.op === "update") {
    const keys = Object.keys(plan.patch ?? {});
    if (!keys.length) throw new DbCompileError("update needs a non-empty patch");
    const sets = keys.map((k) => `${col(k)} = ${value(k, plan.patch![k])}`);
    return { text: `update ${table} set ${sets.join(", ")}${where()}${returning()}`, values };
  }

  return { text: `delete from ${table}${where()}${returning()}`, values };
}

function quote(name: string): string {
  return `"${name.replace(/"/g, '""')}"`;
}
