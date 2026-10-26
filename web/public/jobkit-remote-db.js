/**
 * Supabase-shaped query client for Postgres setups. Each chain records a QueryPlan and posts it to
 * /api/db; the Worker runs it as the signed-in owner. Mirrors src/db/postgres.ts.
 */
export function createRemoteClient(fetchImpl = (...args) => fetch(...args), redirect = (url) => location.replace(url)) {
  const json = { "Content-Type": "application/json" };

  async function run(plan) {
    let res;
    try {
      res = await fetchImpl("/api/db", { method: "POST", headers: json, credentials: "same-origin", body: JSON.stringify(plan) });
    } catch (e) {
      return { data: null, error: { message: String(e && e.message || e) } };
    }
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) {
      const here = typeof location === "undefined" ? "/" : location.pathname + location.search;
      redirect("/login?next=" + encodeURIComponent(here));
      return { data: null, error: { message: body.error || "Unauthorized" } };
    }
    if (res.status === 200) return body;
    return { data: null, error: { message: body.error || "Request failed (" + res.status + ")" } };
  }

  function query(plan) {
    const next = (change) => query({ ...plan, ...change });
    const mutated = () => plan.op !== "select";
    return {
      select(columns, opts) {
        if (mutated()) return next({ returning: columns ?? "*" });
        const change = { op: "select", columns };
        if (opts?.count) change.count = opts.count;
        if (opts?.head) change.head = opts.head;
        return next(change);
      },
      insert: (rows) => next({ op: "insert", rows: Array.isArray(rows) ? rows : [rows] }),
      update: (patch) => next({ op: "update", patch }),
      upsert: (rows, opts) => next({ op: "upsert", rows: Array.isArray(rows) ? rows : [rows], ...(opts?.onConflict ? { onConflict: opts.onConflict } : {}) }),
      delete: () => next({ op: "delete" }),
      eq: (column, value) => next({ filters: [...plan.filters, { column, op: "eq", value }] }),
      in: (column, value) => next({ filters: [...plan.filters, { column, op: "in", value }] }),
      gte: (column, value) => next({ filters: [...plan.filters, { column, op: "gte", value }] }),
      order: (column, opts) => next({ order: [...plan.order, { column, ascending: opts?.ascending ?? true, ...(opts?.nullsFirst !== undefined ? { nullsFirst: opts.nullsFirst } : {}) }] }),
      limit: (n) => next({ limit: n }),
      single: () => next({ cardinality: "single" }),
      maybeSingle: () => next({ cardinality: "maybeSingle" }),
      then: (onfulfilled, onrejected) => run(plan).then(onfulfilled, onrejected),
    };
  }

  return {
    from: (table) => query({ table, op: "select", filters: [], order: [] }),
    auth: {
      async getSession() {
        try {
          const res = await fetchImpl("/api/session", { credentials: "same-origin" });
          if (!res.ok) return { data: { session: null } };
          const { userId } = await res.json();
          return { data: { session: userId ? { user: { id: userId } } : null } };
        } catch {
          return { data: { session: null } };
        }
      },
      async signOut() {
        await fetchImpl("/api/auth/sign-out", { method: "POST", headers: json, credentials: "same-origin", body: "{}" }).catch(() => {});
        return { error: null };
      },
    },
  };
}
