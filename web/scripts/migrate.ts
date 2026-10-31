// Idempotent, concurrency-safe migration runner + first-admin seed.
//   pnpm migrate                      (local)       node migrate.cjs     (inside the app image / compose `migrate` service)
// Env: DATABASE_URL, MIGRATIONS_DIR (default ../db/migrations), ADMIN_EMAIL / ADMIN_PASSWORD (seed only when users is empty)
import { join } from "node:path";
import { Pool } from "pg";
import bcrypt from "bcryptjs";
import { loadMigrations, plan } from "./migrations-lib";

const dir = process.env.MIGRATIONS_DIR ?? join(process.cwd(), "..", "db", "migrations");
const pool = new Pool({ connectionString: process.env.DATABASE_URL ?? "postgres://eyeai:eyeai@localhost:5432/eyeai" });

async function waitForDb() {
  for (let i = 0; i < 60; i++) {
    try { await pool.query("SELECT 1"); return; } catch { await new Promise((r) => setTimeout(r, 1000)); }
  }
  throw new Error("database not reachable after 60 s");
}

async function main() {
  const all = loadMigrations(dir);
  await waitForDb();
  const c = await pool.connect();
  try {
    await c.query("SELECT pg_advisory_lock(727274)");          // several replicas may start at once; one migrates, the rest wait
    await c.query("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, at timestamptz NOT NULL DEFAULT now())");
    await c.query("ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum text");
    const rows = (await c.query("SELECT name, checksum FROM schema_migrations")).rows as { name: string; checksum: string | null }[];
    const applied = new Map(rows.map((r) => [r.name, r.checksum]));
    const pending = plan(all, applied);
    for (const m of pending) {
      await c.query("BEGIN");
      try {
        await c.query(m.sql);
        await c.query("INSERT INTO schema_migrations (name, checksum) VALUES ($1,$2)", [m.name, m.checksum]);
        await c.query("COMMIT");
      } catch (e) { await c.query("ROLLBACK"); throw new Error(`migration ${m.name} failed: ${(e as Error).message}`); }
      console.log("applied", m.name);
    }
    for (const r of rows) if (!r.checksum) {                    // databases migrated before checksums existed: adopt them
      const m = all.find((x) => x.name === r.name);
      if (m) await c.query("UPDATE schema_migrations SET checksum=$2 WHERE name=$1", [m.name, m.checksum]);
    }
    if (!pending.length) console.log(`schema up to date (${all.length} migrations)`);

    const email = process.env.ADMIN_EMAIL, pw = process.env.ADMIN_PASSWORD;
    if (email && pw && !(await c.query("SELECT 1 FROM users LIMIT 1")).rowCount) {
      await c.query("INSERT INTO users (email,name,password_hash,role) VALUES (lower($1),$2,$3,'admin')", [email, process.env.ADMIN_NAME ?? "Administrator", await bcrypt.hash(pw, 10)]);
      console.log("seeded admin", email);
    }
    await c.query("SELECT pg_advisory_unlock(727274)");
  } finally { c.release(); await pool.end(); }
}
main().catch((e) => { console.error(e.message ?? e); process.exit(1); });
