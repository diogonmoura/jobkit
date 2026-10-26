import { neon, types as pgTypes } from "@neondatabase/serverless";
import { compile, DbCompileError } from "./compile";
import { POSTGRES_MIGRATIONS } from "./postgres-sql.generated";
import { TABLES } from "./tables";
import type { BrowserAccess, Db, DbQuery, DbResult, QueryPlan } from "./types";

// Result shapes follow PostgREST: date and timestamp values stay Postgres text (full microsecond
// precision, so they compare equal in a later .eq()), bigint and numeric become numbers.
const asText = (v: string) => v;
export const TYPE_PARSERS: Record<number, (v: string) => unknown> = {
  1082: asText,
  1114: asText,
  1184: asText,
  20: Number,
  1700: Number,
};

export type SqlRunner = (text: string, values: unknown[]) => Promise<Record<string, unknown>[]>;

// Thin wrapper over Neon's HTTP driver: one statement per call.
export function neonRunner(databaseUrl: string): SqlRunner {
  const sql = neon(databaseUrl);
  // The type parsers must go on each query: neon() ignores `types` in its own options.
  // Per-query `types` replaces pg-types entirely, so fall back to its defaults (bool, int4, jsonb...).
  const types = { getTypeParser: (oid: number, fmt?: "text" | "binary") => TYPE_PARSERS[oid] ?? pgTypes.getTypeParser(oid, fmt) };
  return async (text, values) => (await sql.query(text, values, { types })) as Record<string, unknown>[];
}

type Scope = { ownerId?: string; access?: BrowserAccess };

// Runs the Db interface on Postgres. With scope.ownerId set, every query on an owned table is
// limited to that owner and every written row belongs to them.
export function createPostgresDb(run: SqlRunner, scope: Scope): Db {
  return { from: (table) => new Query(run, scope, { table, op: "select", filters: [], order: [] }) };
}

// Runs a finished plan, as received from the browser. conflictOwner is internal: scoped() sets it,
// so a value that came with the plan is dropped here.
export function runPlan(run: SqlRunner, scope: Scope, plan: QueryPlan): PromiseLike<DbResult<any>> {
  return new Query(run, scope, { ...plan, conflictOwner: undefined });
}

class Query implements DbQuery {
  constructor(private run: SqlRunner, private scope: Scope, private plan: QueryPlan) {}

  private next(change: Partial<QueryPlan>): Query {
    return new Query(this.run, this.scope, { ...this.plan, ...change });
  }
  private mutated() {
    return this.plan.op !== "select";
  }

  select(columns?: string, opts?: { count?: "exact"; head?: boolean }): Query {
    if (this.mutated()) return this.next({ returning: columns ?? "*" });
    return this.next({ op: "select", columns, count: opts?.count, head: opts?.head });
  }
  insert(rows: Record<string, unknown> | Record<string, unknown>[]): Query {
    return this.next({ op: "insert", rows: Array.isArray(rows) ? rows : [rows] });
  }
  update(patch: Record<string, unknown>): Query {
    return this.next({ op: "update", patch });
  }
  upsert(rows: Record<string, unknown> | Record<string, unknown>[], opts?: { onConflict?: string }): Query {
    return this.next({ op: "upsert", rows: Array.isArray(rows) ? rows : [rows], onConflict: opts?.onConflict });
  }
  delete(): Query {
    return this.next({ op: "delete" });
  }
  eq(column: string, value: unknown): Query {
    return this.next({ filters: [...this.plan.filters, { column, op: "eq", value }] });
  }
  in(column: string, values: unknown[]): Query {
    return this.next({ filters: [...this.plan.filters, { column, op: "in", value: values }] });
  }
  gte(column: string, value: unknown): Query {
    return this.next({ filters: [...this.plan.filters, { column, op: "gte", value }] });
  }
  order(column: string, opts?: { ascending?: boolean; nullsFirst?: boolean }): Query {
    return this.next({ order: [...this.plan.order, { column, ascending: opts?.ascending ?? true, nullsFirst: opts?.nullsFirst }] });
  }
  limit(n: number): Query {
    return this.next({ limit: n });
  }
  single(): Query {
    return this.next({ cardinality: "single" });
  }
  maybeSingle(): Query {
    return this.next({ cardinality: "maybeSingle" });
  }

  // Adds the owner filter and owner column. Unknown tables are left for compile to reject.
  private scoped(): QueryPlan {
    const ownerId = this.scope.ownerId;
    const plan = this.plan;
    if (ownerId === undefined || !Object.hasOwn(TABLES, plan.table) || !TABLES[plan.table].owned) return plan;
    return {
      ...plan,
      filters: [...plan.filters, { column: "user_id", op: "eq", value: ownerId }],
      rows: plan.rows?.map((r) => ({ ...r, user_id: ownerId })),
      conflictOwner: plan.op === "upsert" ? ownerId : undefined,
      patch: plan.patch ? { ...plan.patch, user_id: ownerId } : plan.patch,
    };
  }

  private async exec(): Promise<DbResult<any>> {
    const plan = this.plan;
    const { access } = this.scope;
    if (access && !(Object.hasOwn(access, plan.table) && access[plan.table].includes(plan.op))) {
      return { data: null, error: { message: `${plan.op} on ${plan.table} is not allowed`, code: "42501" } };
    }
    try {
      const { text, values } = compile(this.scoped(), TABLES);
      const rows = await this.run(text, values);
      if (plan.op === "select" && plan.head) return { data: null, error: null, count: Number(rows[0]?.count ?? 0) };
      if (plan.op !== "select" && !plan.returning) return { data: null, error: null };
      if (plan.cardinality) {
        if (rows.length === 1) return { data: rows[0], error: null };
        if (rows.length === 0 && plan.cardinality === "maybeSingle") return { data: null, error: null };
        return {
          data: null,
          error: { message: `JSON object requested, multiple (or no) rows returned (${rows.length} rows)`, code: "PGRST116" },
        };
      }
      return { data: rows, error: null };
    } catch (e) {
      if (e instanceof DbCompileError) return { data: null, error: { message: e.message, code: "PGRST100" } };
      const err = e as { message?: string; code?: string };
      return { data: null, error: { message: err?.message ?? String(e), code: err?.code } };
    }
  }

  then<R1 = DbResult, R2 = never>(
    onfulfilled?: ((value: DbResult) => R1 | PromiseLike<R1>) | null,
    onrejected?: ((reason: any) => R2 | PromiseLike<R2>) | null,
  ): PromiseLike<R1 | R2> {
    return this.exec().then(onfulfilled, onrejected);
  }
}

// Splits a SQL file into statements. Understands quotes, comments and dollar-quoted bodies;
// enough for the files in postgres/, not a general parser.
function splitStatements(sql: string): string[] {
  const out: string[] = [];
  let cur = "";
  for (let i = 0; i < sql.length; ) {
    const rest = sql.slice(i);
    let m: RegExpMatchArray | null;
    if ((m = rest.match(/^--[^\n]*/))) {
      i += m[0].length;
    } else if ((m = rest.match(/^\/\*[\s\S]*?\*\//))) {
      i += m[0].length;
    } else if ((m = rest.match(/^'(?:[^']|'')*'/)) || (m = rest.match(/^"(?:[^"]|"")*"/))) {
      cur += m[0];
      i += m[0].length;
    } else if ((m = rest.match(/^\$([A-Za-z_]\w*)?\$/))) {
      const end = sql.indexOf(m[0], i + m[0].length);
      const stop = end < 0 ? sql.length : end + m[0].length;
      cur += sql.slice(i, stop);
      i = stop;
    } else if (sql[i] === ";") {
      if (cur.trim()) out.push(cur.trim());
      cur = "";
      i++;
    } else {
      cur += sql[i++];
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

// Raw parameterized SQL on purpose: jobkit_schema_migrations is not in TABLES.
export async function pendingMigrations(run: SqlRunner): Promise<string[]> {
  const exists = await run("select to_regclass('jobkit_schema_migrations') as t", []);
  const applied = new Set<string>();
  if (exists[0]?.t) for (const r of await run("select id from jobkit_schema_migrations", [])) applied.add(String(r.id));
  return POSTGRES_MIGRATIONS.filter((m) => !applied.has(m.id)).map((m) => m.id);
}

// Runs each pending migration statement by statement (the HTTP driver takes one at a time),
// then records its id. Migration SQL is idempotent, so a half-run migration can be retried.
export async function applyMigrations(run: SqlRunner): Promise<string[]> {
  const pending = new Set(await pendingMigrations(run));
  const done: string[] = [];
  for (const m of POSTGRES_MIGRATIONS) {
    if (!pending.has(m.id)) continue;
    for (const stmt of splitStatements(m.sql)) await run(stmt, []);
    await run("insert into jobkit_schema_migrations (id) values ($1) on conflict (id) do nothing", [m.id]);
    done.push(m.id);
  }
  return done;
}
