import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export type Migration = { name: string; sql: string; checksum: string };

/** Migrations are plain .sql files applied in filename order (001_..., 002_...). Names must be unique and sortable. */
export function loadMigrations(dir: string): Migration[] {
  const files = readdirSync(dir).filter((f) => f.endsWith(".sql")).sort();
  for (const f of files) if (!/^\d{3,}_[a-z0-9_]+\.sql$/.test(f)) throw new Error(`bad migration name: ${f} (expected 001_description.sql)`);
  return files.map((name) => {
    const sql = readFileSync(join(dir, name), "utf8");
    return { name, sql, checksum: createHash("sha256").update(sql).digest("hex") };
  });
}

/** Which migrations still need to run; throws if an already-applied one was edited (never rewrite history). */
export function plan(all: Migration[], applied: Map<string, string | null>): Migration[] {
  const pending: Migration[] = [];
  for (const m of all) {
    if (!applied.has(m.name)) { pending.push(m); continue; }
    const was = applied.get(m.name);
    if (was && was !== m.checksum) throw new Error(`migration ${m.name} was modified after it was applied; add a new migration instead`);
  }
  return pending;
}
