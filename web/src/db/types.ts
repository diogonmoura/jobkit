// The query surface the app uses today, in supabase-js style. Nothing else is supported.
export type DbError = { message: string; code?: string };
// T defaults to any[] like an untyped supabase-js list result, so .map callbacks keep their contextual types.
export type DbResult<T = any[]> = { data: T | null; error: DbError | null; count?: number | null };

export interface DbQuery<T = any[]> extends PromiseLike<DbResult<T>> {
  select(columns?: string, opts?: { count?: "exact"; head?: boolean }): DbQuery<T>;
  insert(rows: Record<string, unknown> | Record<string, unknown>[], opts?: { count?: "exact" }): DbQuery<T>;
  update(patch: Record<string, unknown>): DbQuery<T>;
  upsert(rows: Record<string, unknown> | Record<string, unknown>[], opts?: { onConflict?: string }): DbQuery<T>;
  delete(): DbQuery<T>;
  eq(column: string, value: unknown): DbQuery<T>;
  in(column: string, values: unknown[]): DbQuery<T>;
  gte(column: string, value: unknown): DbQuery<T>;
  order(column: string, opts?: { ascending?: boolean; nullsFirst?: boolean }): DbQuery<T>;
  limit(n: number): DbQuery<T>;
  single(): DbQuery<any>;
  maybeSingle(): DbQuery<any>;
}

// What a browser-facing db may do, per table. createPostgresDb rejects anything else with code 42501.
export type BrowserAccess = Record<string, Array<QueryPlan["op"]>>;

export interface Db { from(table: string): DbQuery }

// What a chain records before it runs. `compile` turns it into SQL.
export type QueryPlan = {
  table: string;
  op: "select" | "insert" | "update" | "upsert" | "delete";
  columns?: string;
  count?: "exact";
  head?: boolean;
  rows?: Record<string, unknown>[];
  patch?: Record<string, unknown>;
  onConflict?: string;
  // Upsert only: the do-update applies just to existing rows owned by this user.
  conflictOwner?: string;
  returning?: string;
  filters: { column: string; op: "eq" | "in" | "gte"; value: unknown }[];
  order: { column: string; ascending: boolean; nullsFirst?: boolean }[];
  limit?: number;
  cardinality?: "single" | "maybeSingle";
};

// The allowlist for one table: only these columns may appear in SQL.
// primaryKey is the conflict target for an upsert without onConflict.
export type TableSpec = { columns: string[]; jsonColumns: string[]; owned: boolean; primaryKey: string[] };
