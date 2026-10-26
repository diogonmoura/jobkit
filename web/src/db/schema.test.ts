import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { TABLES } from "./tables";
import { POSTGRES_MIGRATIONS } from "./postgres-sql.generated";

const root = path.resolve(import.meta.dirname, "../..");
const sqlFiles = (dir: string) =>
  fs.readdirSync(path.join(root, dir)).filter((f) => f.endsWith(".sql")).sort().map((f) => fs.readFileSync(path.join(root, dir, f), "utf8"));

type Parsed = { columns: string[]; primaryKey: string[] };

// Reads `create table` and `add column` statements; enough for the SQL in this repo, not a SQL parser.
function parseSchema(sources: string[]): Record<string, Parsed> {
  const tables: Record<string, Parsed> = {};
  for (const raw of sources) {
    const sql = raw.replace(/--.*$/gm, "");
    for (const m of sql.matchAll(/create table if not exists\s+(?:public\.)?(jobkit_\w+)\s*\(/gi)) {
      let depth = 1;
      let i = m.index! + m[0].length;
      const start = i;
      for (; i < sql.length && depth > 0; i++) depth += sql[i] === "(" ? 1 : sql[i] === ")" ? -1 : 0;
      const body = sql.slice(start, i - 1);
      const parts: string[] = [];
      let level = 0;
      let cur = "";
      for (const ch of body) {
        if (ch === "(") level++;
        if (ch === ")") level--;
        if (ch === "," && level === 0) {
          parts.push(cur);
          cur = "";
        } else cur += ch;
      }
      parts.push(cur);
      const t: Parsed = { columns: [], primaryKey: [] };
      for (const part of parts.map((p) => p.trim().replace(/\s+/g, " "))) {
        const tablePk = part.match(/^primary key \(([^)]+)\)/i);
        if (tablePk) t.primaryKey = tablePk[1].split(",").map((c) => c.trim());
        else if (/^(constraint|foreign key|unique|check)\b/i.test(part)) continue;
        else {
          const name = part.split(" ")[0];
          t.columns.push(name);
          if (/\bprimary key\b/i.test(part)) t.primaryKey = [name];
        }
      }
      tables[m[1]] = t;
    }
    for (const m of sql.matchAll(/alter table\s+(?:public\.)?(jobkit_\w+)\s+([^;]*);/gi)) {
      for (const c of m[2].matchAll(/add column if not exists (\w+)/gi)) if (!tables[m[1]].columns.includes(c[1])) tables[m[1]].columns.push(c[1]);
    }
  }
  return tables;
}

const supabase = parseSchema([fs.readFileSync(path.join(root, "supabase/schema.sql"), "utf8"), ...sqlFiles("supabase/migrations")]);
// The migrations bookkeeping table only exists on the Postgres side.
const { jobkit_schema_migrations: _bookkeeping, ...postgres } = parseSchema([
  fs.readFileSync(path.join(root, "postgres/schema.sql"), "utf8"),
  ...sqlFiles("postgres/migrations"),
]);

const sorted = (a: string[]) => [...a].sort();

describe("schema parity", () => {
  it("has the same tables and columns as the Supabase schema", () => {
    assert.deepEqual(sorted(Object.keys(postgres)), sorted(Object.keys(supabase)));
    for (const t of Object.keys(supabase)) {
      assert.deepEqual(sorted(postgres[t].columns), sorted(supabase[t].columns), t);
      assert.deepEqual(postgres[t].primaryKey, supabase[t].primaryKey, `${t} primary key`);
    }
  });
});

describe("allowlist matches schema", () => {
  it("lists exactly the tables, columns and primary keys of the Supabase schema", () => {
    assert.deepEqual(sorted(Object.keys(TABLES)), sorted(Object.keys(supabase)));
    for (const [name, spec] of Object.entries(TABLES)) {
      assert.deepEqual(sorted(spec.columns), sorted(supabase[name].columns), name);
      assert.deepEqual(spec.primaryKey, supabase[name].primaryKey, `${name} primary key`);
      assert.equal(spec.owned, supabase[name].columns.includes("user_id"), `${name} owned`);
    }
  });

  it("marks json and jsonb columns", async () => {
    const db = new PGlite();
    for (const m of POSTGRES_MIGRATIONS) await db.exec(m.sql);
    const res = await db.query<{ table_name: string; column_name: string }>(
      "select table_name, column_name from information_schema.columns where table_schema = 'public' and data_type in ('json', 'jsonb')",
    );
    const actual: Record<string, string[]> = {};
    for (const r of res.rows) (actual[r.table_name] ??= []).push(r.column_name);
    for (const [name, spec] of Object.entries(TABLES)) assert.deepEqual(sorted(spec.jsonColumns), sorted(actual[name] ?? []), name);
    await db.close();
  });
});

describe("postgres schema", () => {
  it("applies twice without error", async () => {
    const db = new PGlite();
    assert.equal(POSTGRES_MIGRATIONS[0].id, "0000_schema.sql");
    for (let run = 0; run < 2; run++) for (const m of POSTGRES_MIGRATIONS) await db.exec(m.sql);
    await db.close();
  });

  it("moves updated_at when an application changes", async () => {
    const db = new PGlite();
    for (const m of POSTGRES_MIGRATIONS) await db.exec(m.sql);
    const user = "00000000-0000-0000-0000-000000000001";
    await db.query("insert into jobkit_apps (user_id, id, company, role, applied) values ($1, 'a', 'Acme', 'PM', '2026-10-01')", [user]);
    const before = (await db.query<{ t: number }>("select extract(epoch from updated_at)::float8 as t from jobkit_apps")).rows[0].t;
    await db.exec("select pg_sleep(0.02)");
    await db.query("update jobkit_apps set status = 'ack' where user_id = $1 and id = 'a'", [user]);
    const after = (await db.query<{ t: number }>("select extract(epoch from updated_at)::float8 as t from jobkit_apps")).rows[0].t;
    assert.ok(after > before, `${after} should be greater than ${before}`);
    await db.close();
  });
});
