import { query, one } from "./db";
import type { Action, Condition, ConnectionKind, StoredConfig } from "../automations";

export type ConnectionRow = { id: string; name: string; kind: ConnectionKind; config: StoredConfig; enabled: boolean; updated_at: string };
export type AutomationRow = {
  id: string; name: string; description: string; enabled: boolean; mode: "simulate" | "live"; conditions: Condition[]; actions: Action[];
  cooldown_s: number; max_per_hour: number; position: number; updated_at: string;
};
export type RunListRow = {
  id: number; automation_id: string | null; automation_name: string | null; connection_id: string | null; connection_name: string | null;
  event_id: string | null; event_code: string | null; source_id: string | null; source_code: string | null; source_name: string | null;
  mode: string; status: string; detail: Record<string, unknown>; requested_by: string | null; at: string; finished_at: string | null;
};

const CONN = "id, name, kind, config, enabled, updated_at";
const AUTO = "id, name, description, enabled, mode, conditions, actions, cooldown_s, max_per_hour, position, updated_at";

export const listConnections = () => query<ConnectionRow>(`SELECT ${CONN} FROM connections ORDER BY name`);
export const getConnection = async (id: string) => (await query<ConnectionRow>(`SELECT ${CONN} FROM connections WHERE id = $1`, [id]))[0] ?? null;
export const insertConnection = (name: string, kind: string, config: StoredConfig, enabled: boolean, userId: string) =>
  one<ConnectionRow>(`INSERT INTO connections (name, kind, config, enabled, created_by) VALUES ($1,$2,$3::jsonb,$4,$5) RETURNING ${CONN}`, [name, kind, JSON.stringify(config), enabled, userId]);
export async function updateConnection(id: string, p: { name?: string; config?: StoredConfig; enabled?: boolean }) {
  const sets: string[] = [], v: unknown[] = [];
  if (p.name !== undefined) { v.push(p.name); sets.push(`name = $${v.length}`); }
  if (p.config !== undefined) { v.push(JSON.stringify(p.config)); sets.push(`config = $${v.length}::jsonb`); }
  if (p.enabled !== undefined) { v.push(p.enabled); sets.push(`enabled = $${v.length}`); }
  v.push(id);
  return (await query<ConnectionRow>(`UPDATE connections SET ${sets.join(", ")}, updated_at = now() WHERE id = $${v.length} RETURNING ${CONN}`, v))[0] ?? null;
}
export const deleteConnection = async (id: string) => (await query<{ name: string }>("DELETE FROM connections WHERE id = $1 RETURNING name", [id]))[0] ?? null;
/** Automations that still use a connection (deleting it would silently break them). */
export const automationsUsingConnection = (id: string) =>
  query<{ id: string; name: string }>("SELECT id, name FROM automations WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(actions) a WHERE a->>'connection' = $1) ORDER BY name", [id]);

export const listAutomations = () => query<AutomationRow>(`SELECT ${AUTO} FROM automations ORDER BY position, name`);
export const getAutomation = async (id: string) => (await query<AutomationRow>(`SELECT ${AUTO} FROM automations WHERE id = $1`, [id]))[0] ?? null;
export const insertAutomation = (a: Omit<AutomationRow, "id" | "updated_at">, userId: string) =>
  one<AutomationRow>(
    `INSERT INTO automations (name, description, enabled, mode, conditions, actions, cooldown_s, max_per_hour, position, created_by)
     VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9,$10) RETURNING ${AUTO}`,
    [a.name, a.description, a.enabled, a.mode, JSON.stringify(a.conditions), JSON.stringify(a.actions), a.cooldown_s, a.max_per_hour, a.position, userId],
  );
const JSON_COLS = new Set(["conditions", "actions"]);
export async function updateAutomation(id: string, patch: Partial<Omit<AutomationRow, "id" | "updated_at">>) {
  const sets: string[] = [], v: unknown[] = [];
  for (const [k, val] of Object.entries(patch)) { v.push(JSON_COLS.has(k) ? JSON.stringify(val) : val); sets.push(`${k} = $${v.length}${JSON_COLS.has(k) ? "::jsonb" : ""}`); } // keys come from the validated schema
  v.push(id);
  return (await query<AutomationRow>(`UPDATE automations SET ${sets.join(", ")}, updated_at = now() WHERE id = $${v.length} RETURNING ${AUTO}`, v))[0] ?? null;
}
export const deleteAutomation = async (id: string) => (await query<{ name: string }>("DELETE FROM automations WHERE id = $1 RETURNING name", [id]))[0] ?? null;

/** Test and preview requests: the web app never talks to a target, it queues a row the analysis service picks up within about a second. */
export const queueRun = (r: { automationId: string | null; connectionId: string | null; mode: "test_send" | "test_simulate"; userId: string }) =>
  one<{ id: string }>(
    "INSERT INTO automation_runs (automation_id, connection_id, mode, status, requested_by) VALUES ($1,$2,$3,'queued',$4) RETURNING id::text",
    [r.automationId, r.connectionId, r.mode, r.userId],
  );

const RUN_SELECT = `
  SELECT r.id::int AS id, r.automation_id, a.name AS automation_name, r.connection_id, c.name AS connection_name, r.event_id, e.code AS event_code,
         r.source_id, s.code AS source_code, s.name AS source_name, r.mode, r.status, r.detail, r.requested_by, r.at, r.finished_at
  FROM automation_runs r
  LEFT JOIN automations a ON a.id = r.automation_id
  LEFT JOIN connections c ON c.id = r.connection_id
  LEFT JOIN events e ON e.id = r.event_id
  LEFT JOIN sources s ON s.id = r.source_id`;
export const clampLimit = (raw: string | null, def: number, max = 500) => { const n = Number(raw); return Number.isInteger(n) && n > 0 ? Math.min(n, max) : def; };
export const listRuns = (limit: number) => query<RunListRow>(`${RUN_SELECT} ORDER BY r.at DESC, r.id DESC LIMIT $1`, [limit]);
export const listAutomationRuns = (automationId: string, limit: number) => query<RunListRow>(`${RUN_SELECT} WHERE r.automation_id = $1 ORDER BY r.at DESC, r.id DESC LIMIT $2`, [automationId, limit]);
