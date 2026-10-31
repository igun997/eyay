import { Pool, type QueryResultRow } from "pg";

const g = globalThis as unknown as { __pool?: Pool };

export const pool =
  g.__pool ??
  (g.__pool = new Pool({
    connectionString: process.env.DATABASE_URL ?? "postgres://eyeai:eyeai@localhost:5432/eyeai",
    max: 10,
  }));

export async function query<T extends QueryResultRow = QueryResultRow>(text: string, params: unknown[] = []) {
  const r = await pool.query<T>(text, params);
  return r.rows;
}

export async function one<T extends QueryResultRow = QueryResultRow>(text: string, params: unknown[] = []) {
  return (await query<T>(text, params))[0] ?? null;
}

export async function tx<T>(fn: (q: typeof query) => Promise<T>): Promise<T> {
  const c = await pool.connect();
  try {
    await c.query("BEGIN");
    const q = async <R extends QueryResultRow = QueryResultRow>(t: string, p: unknown[] = []) => (await c.query<R>(t, p)).rows;
    const out = await fn(q as typeof query);
    await c.query("COMMIT");
    return out;
  } catch (e) {
    await c.query("ROLLBACK");
    throw e;
  } finally {
    c.release();
  }
}
