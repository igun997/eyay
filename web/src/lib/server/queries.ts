import { query, one } from "./db";
import { type EngineConfig, compact } from "../engine-config";
import type { BehaviorRule, CategoryRow, EventRow, Frame, IncidentRow, SourceRow, TimelineRow, UserRow } from "../types";

const EVENT_SELECT = `
  SELECT e.id, e.code, e.title, e.label, e.confidence, e.severity, e.status, e.bbox, e.detected_at,
         e.source_id, s.code AS source_code, s.name AS source_name,
         e.incident_id, i.code AS incident_code, e.dismiss_reason, e.note, e.track_id,
         (SELECT count(*)::int FROM event_snapshots x WHERE x.event_id = e.id) AS snapshots,
         e.pipeline, e.cls_label, e.cls_confidence, e.cls_probs, e.cls_model, e.cls_ms, e.cls_error,
         e.indication_kind, e.indication, e.burst_ms, e.burst_done, e.cls_flags, e.suspicious, e.speed_kmh
  FROM events e
  JOIN sources s ON s.id = e.source_id
  LEFT JOIN incidents i ON i.id = e.incident_id`;

export type EventFilter = { tab?: "needs_review" | "in_pipeline" | "resolved"; sourceId?: string; label?: string; severity?: string; q?: string; sort?: "newest" | "oldest"; limit?: number };

export function listEvents(f: EventFilter = {}) {
  const w: string[] = [];
  const p: unknown[] = [];
  const bind = (v: unknown) => (p.push(v), `$${p.length}`);
  // an event is reviewable once the classifier is done with it (ready) or gave up / is not configured (failed, skipped)
  if (f.tab === "needs_review") w.push("e.status = 'ready_for_triage' AND e.pipeline IN ('ready','failed','skipped')");
  if (f.tab === "in_pipeline") w.push("e.status = 'ready_for_triage' AND e.pipeline IN ('queued','classifying')");
  if (f.tab === "resolved") w.push("e.status IN ('confirmed','dismissed')");
  if (f.sourceId) w.push(`e.source_id = ${bind(f.sourceId)}`);
  if (f.label) w.push(`e.label = ${bind(f.label)}`);
  if (f.severity) w.push(`e.severity = ${bind(f.severity)}`);
  if (f.q) {
    const n = bind(`%${f.q}%`);
    w.push(`(e.title ILIKE ${n} OR s.code ILIKE ${n} OR e.code ILIKE ${n} OR i.code ILIKE ${n})`);
  }
  const where = w.length ? "WHERE " + w.join(" AND ") : "";
  return query<EventRow>(
    `${EVENT_SELECT} ${where} ORDER BY e.detected_at ${f.sort === "oldest" ? "ASC" : "DESC"} LIMIT ${Math.min(f.limit ?? 100, 500)}`,
    p,
  );
}

export const eventCounts = () =>
  one<{ needs_review: number; in_pipeline: number; resolved: number }>(
    `SELECT count(*) FILTER (WHERE status='ready_for_triage' AND pipeline IN ('ready','failed','skipped'))::int AS needs_review,
            count(*) FILTER (WHERE status='ready_for_triage' AND pipeline IN ('queued','classifying'))::int AS in_pipeline,
            count(*) FILTER (WHERE status<>'ready_for_triage')::int AS resolved FROM events`,
  );

export const getEvent = async (id: string) => (await query<EventRow>(`${EVENT_SELECT} WHERE e.id = $1`, [id]))[0] ?? null;

const INC_SELECT = `
  SELECT i.id, i.code, i.title, i.severity, i.status, i.label, i.source_id, s.code AS source_code, s.name AS source_name,
         i.assigned_to, u.name AS assignee_name, i.opened_at, i.resolved_at,
         (SELECT count(*)::int FROM events e WHERE e.incident_id = i.id) AS events
  FROM incidents i
  LEFT JOIN sources s ON s.id = i.source_id
  LEFT JOIN users u ON u.id = i.assigned_to`;

const SEV_ORDER = `CASE i.severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END`;

export const listIncidents = (status?: "active" | "resolved") =>
  query<IncidentRow>(
    `${INC_SELECT} ${status === "active" ? "WHERE i.status <> 'resolved'" : status === "resolved" ? "WHERE i.status = 'resolved'" : ""}
     ORDER BY ${status === "resolved" ? "i.resolved_at DESC NULLS LAST" : `${SEV_ORDER}, i.opened_at DESC`} LIMIT 200`,
  );

export const getIncident = async (idOrCode: string) =>
  (await query<IncidentRow>(`${INC_SELECT} WHERE i.id::text = $1 OR i.code = $1`, [idOrCode]))[0] ?? null;

export const incidentTimeline = (id: string) =>
  query<TimelineRow>(
    `SELECT t.id, t.kind, t.title, t.detail, t.at, u.name AS user_name, t.event_id
     FROM incident_timeline t LEFT JOIN users u ON u.id = t.user_id WHERE t.incident_id = $1 ORDER BY t.at, t.id`,
    [id],
  );

export const incidentEvents = (id: string) => query<EventRow>(`${EVENT_SELECT} WHERE e.incident_id = $1 ORDER BY e.detected_at`, [id]);

export const listSources = () =>
  query<SourceRow>(
    `SELECT id, code, name, url, kind, labels, min_confidence, enabled, status, last_error, last_seen, fps, inference_ms, config
     FROM sources ORDER BY code`,
  );

export const getSource = async (id: string) =>
  (await query<SourceRow>(
    `SELECT id, code, name, url, kind, labels, min_confidence, enabled, status, last_error, last_seen, fps, inference_ms, config
     FROM sources WHERE id = $1`, [id]))[0] ?? null;

export const listCategories = () => query<CategoryRow>("SELECT * FROM categories ORDER BY severity, label");

export async function updateCategory(label: string, patch: Partial<Omit<CategoryRow, "label">>) {
  const sets: string[] = [], p: unknown[] = [];
  for (const [k, v] of Object.entries(patch)) { p.push(v); sets.push(`${k} = $${p.length}`); } // keys come from the validated schema, never from the client
  p.push(label);
  return (await query<CategoryRow>(`UPDATE categories SET ${sets.join(", ")} WHERE label = $${p.length} RETURNING *`, p))[0] ?? null;
}

export const listUsers = () => query<UserRow>("SELECT id,email,name,role,active,created_at FROM users ORDER BY created_at");

export async function overview() {
  const [counts] = await query<{ open_incidents: number; awaiting: number; online: number; total: number; events_24h: number; confirmed_24h: number; dismissed_24h: number }>(
    `SELECT (SELECT count(*)::int FROM incidents WHERE status <> 'resolved') AS open_incidents,
            (SELECT count(*)::int FROM events WHERE status='ready_for_triage' AND pipeline IN ('ready','failed','skipped')) AS awaiting,
            (SELECT count(*)::int FROM sources WHERE status='online' AND enabled) AS online,
            (SELECT count(*)::int FROM sources WHERE enabled) AS total,
            (SELECT count(*)::int FROM events WHERE detected_at > now() - interval '24 hours') AS events_24h,
            (SELECT count(*)::int FROM events WHERE status='confirmed' AND detected_at > now() - interval '24 hours') AS confirmed_24h,
            (SELECT count(*)::int FROM events WHERE status='dismissed' AND detected_at > now() - interval '24 hours') AS dismissed_24h`,
  );
  const [worker] = await query<{ last_seen: string; sources: number; model: string | null; classifier: string | null }>(
    "SELECT last_seen, sources, model, classifier FROM worker_heartbeats ORDER BY last_seen DESC LIMIT 1",
  );
  const [pipeline] = await query<{ queued: number; classifying: number; ready: number; failed: number }>(
    `SELECT count(*) FILTER (WHERE pipeline='queued')::int AS queued, count(*) FILTER (WHERE pipeline='classifying')::int AS classifying,
            count(*) FILTER (WHERE pipeline='ready' AND status='ready_for_triage')::int AS ready,
            count(*) FILTER (WHERE pipeline='failed' AND status='ready_for_triage')::int AS failed
     FROM events WHERE status='ready_for_triage'`,
  );
  const byLabel = await query<{ label: string; n: number }>(
    "SELECT label, count(*)::int AS n FROM events WHERE detected_at > now() - interval '24 hours' GROUP BY label ORDER BY n DESC",
  );
  return { counts, worker: worker ?? null, pipeline, byLabel, attention: (await listIncidents("active")).slice(0, 5) };
}

/** Strip user:password from stream URLs. Only admins (who register them) get the real value. */
export const maskCreds = <T extends { url: string }>(s: T): T => ({ ...s, url: s.url.replace(/\/\/([^:@/]+):([^@/]+)@/, "//$1:••••@") });

const RULE_COLS = "key,title,description,kind,question,applies_to,threshold,config,severity,opens_incident,enabled,position";
export const listBehaviorRules = () => query<BehaviorRule>(`SELECT ${RULE_COLS} FROM behavior_rules ORDER BY position, key`);
export const getBehaviorRule = async (key: string) => (await query<BehaviorRule>(`SELECT ${RULE_COLS} FROM behavior_rules WHERE key = $1`, [key]))[0] ?? null;

type RuleWrite = Omit<BehaviorRule, "key">;
const ruleParams = (r: RuleWrite) => [r.title, r.description, r.kind, r.question, r.applies_to, r.threshold, JSON.stringify(r.config ?? {}), r.severity, r.opens_incident, r.enabled, r.position];
export const insertBehaviorRule = (key: string, r: RuleWrite) =>
  one<BehaviorRule>(
    `INSERT INTO behavior_rules (key,title,description,kind,question,applies_to,threshold,config,severity,opens_incident,enabled,position)
     VALUES ($12,$1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11) RETURNING ${RULE_COLS}`,
    [...ruleParams(r), key],
  );
export const updateBehaviorRule = async (key: string, r: RuleWrite) =>
  (await query<BehaviorRule>(
    `UPDATE behavior_rules SET title=$1,description=$2,kind=$3,question=$4,applies_to=$5,threshold=$6,config=$7::jsonb,severity=$8,opens_incident=$9,enabled=$10,position=$11
     WHERE key=$12 RETURNING ${RULE_COLS}`,
    [...ruleParams(r), key],
  ))[0] ?? null;
export const deleteBehaviorRule = async (key: string) => (await query("DELETE FROM behavior_rules WHERE key = $1 RETURNING key", [key])).length > 0;

// ---- engine configuration (docs/ENGINE_CONFIG.md) -------------------------------------------------------------------
export async function getEngineDefaults(): Promise<EngineConfig> {
  const r = await one<{ value: EngineConfig }>("SELECT value FROM engine_settings WHERE key = 'defaults'");
  return compact(r?.value);
}
export const setEngineDefaults = (cfg: EngineConfig, userId: string) =>
  query(
    `INSERT INTO engine_settings (key, value, updated_by) VALUES ('defaults', $1::jsonb, $2)
     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by`,
    [JSON.stringify(cfg), userId],
  );
export const setSourceConfig = async (id: string, cfg: EngineConfig) =>
  (await query<{ code: string }>("UPDATE sources SET config = $1::jsonb, updated_at = now() WHERE id = $2 RETURNING code", [JSON.stringify(cfg), id]))[0] ?? null;

export const listFrames = (eventIds: string[]) =>
  eventIds.length
    ? query<Frame>("SELECT event_id, idx, offset_ms, bbox FROM event_snapshots WHERE event_id = ANY($1::uuid[]) ORDER BY event_id, idx", [eventIds])
    : Promise.resolve([] as Frame[]);
