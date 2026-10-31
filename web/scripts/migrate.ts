import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Pool } from "pg";
import bcrypt from "bcryptjs";

const dir = process.env.MIGRATIONS_DIR ?? join(process.cwd(), "..", "db", "migrations");
const pool = new Pool({ connectionString: process.env.DATABASE_URL ?? "postgres://eyeai:eyeai@localhost:5432/eyeai" });

async function waitForDb() {
  for (let i = 0; i < 60; i++) {
    try { await pool.query("SELECT 1"); return; } catch { await new Promise((r) => setTimeout(r, 1000)); }
  }
  throw new Error("database not reachable");
}

async function main() {
  await waitForDb();
  const c = await pool.connect();
  try {
    await c.query("SELECT pg_advisory_lock(727274)");
    await c.query("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, at timestamptz NOT NULL DEFAULT now())");
    for (const f of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
      if ((await c.query("SELECT 1 FROM schema_migrations WHERE name=$1", [f])).rowCount) continue;
      await c.query("BEGIN");
      try { await c.query(readFileSync(join(dir, f), "utf8")); await c.query("INSERT INTO schema_migrations (name) VALUES ($1)", [f]); await c.query("COMMIT"); }
      catch (e) { await c.query("ROLLBACK"); throw e; }
      console.log("applied", f);
    }
    const email = process.env.ADMIN_EMAIL, pw = process.env.ADMIN_PASSWORD;
    if (email && pw && !(await c.query("SELECT 1 FROM users LIMIT 1")).rowCount) {
      await c.query("INSERT INTO users (email,name,password_hash,role) VALUES (lower($1),$2,$3,'admin')", [email, process.env.ADMIN_NAME ?? "Administrator", await bcrypt.hash(pw, 10)]);
      console.log("seeded admin", email);
    }
    await c.query("SELECT pg_advisory_unlock(727274)");
  } finally { c.release(); await pool.end(); }
}
main().catch((e) => { console.error(e); process.exit(1); });
