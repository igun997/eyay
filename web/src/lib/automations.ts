// Alerts and automations contract: docs/AUTOMATIONS.md. Pure (no server imports): the builder and the API share these rules.
import { z } from "zod";

export const SECRET_MASK = "********";
export const KINDS = ["webhook", "mqtt", "modbus"] as const;
export type ConnectionKind = (typeof KINDS)[number];
export const SEVERITY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export const CLASSIFIED_AS = ["person", "vehicle", "false_alarm", "other"] as const;

const uuid = z.string().regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i, "Not a valid id");
const host = z.string().trim().min(1).max(253).regex(/^[A-Za-z0-9._-]+$/, "Use a host name or an IP address");
const port = (d: number) => z.number().int().min(1).max(65535).default(d);
const timeout = z.number().int().min(1).max(30).default(5);

// ---- connections ----------------------------------------------------------------------------------------------------
export const webhookConfig = z.object({
  url: z.string().trim().max(500).regex(/^https?:\/\/[^\s]+$/i, "Use an http:// or https:// address"),
  method: z.enum(["POST", "PUT"]).default("POST"),
  headers: z.record(z.string().regex(/^[A-Za-z0-9-]{1,60}$/, "Header names use letters, digits and -"), z.string().max(500)).default({}),
  secret: z.string().max(200).optional(),
  timeout_s: timeout,
}).strict();
export const mqttConfig = z.object({
  host, port: port(1883), username: z.string().max(200).default(""), password: z.string().max(200).optional(),
  client_id: z.string().trim().min(1).max(64).default("eyeai"), timeout_s: timeout,
}).strict();
export const modbusConfig = z.object({ host, port: port(502), unit_id: z.number().int().min(0).max(255).default(1), timeout_s: timeout }).strict();

const CONFIG_BY_KIND = { webhook: webhookConfig, mqtt: mqttConfig, modbus: modbusConfig } as const;

export const connectionInput = z.object({
  name: z.string().trim().min(1).max(80),
  kind: z.enum(KINDS),
  config: z.record(z.string(), z.unknown()).default({}),
  enabled: z.boolean().default(true),
}).strict();
export const connectionPatch = z.object({
  name: z.string().trim().min(1).max(80), config: z.record(z.string(), z.unknown()), enabled: z.boolean(),
}).partial().strict().refine((v) => Object.keys(v).length > 0, "Nothing to update");

export type StoredConfig = Record<string, unknown>;

/** The fields whose values are never returned and never shown. */
export const isSecretKey = (kind: ConnectionKind, key: string) => (kind === "webhook" && key === "secret") || (kind === "mqtt" && key === "password");

/** `********` stands in for a stored secret (also for header values, which commonly carry API keys). */
export function maskConfig(kind: ConnectionKind, config: StoredConfig): StoredConfig {
  const out: StoredConfig = { ...config };
  for (const k of Object.keys(out)) if (isSecretKey(kind, k) && typeof out[k] === "string" && out[k] !== "") out[k] = SECRET_MASK;
  if (kind === "webhook" && out.headers && typeof out.headers === "object")
    out.headers = Object.fromEntries(Object.entries(out.headers as Record<string, string>).map(([k, v]) => [k, v === "" ? "" : SECRET_MASK]));
  return out;
}

/** Sending `********` (or leaving the key out) keeps the stored value, `""` clears it, anything else replaces it. */
export function mergeSecrets(kind: ConnectionKind, stored: StoredConfig, incoming: StoredConfig): StoredConfig {
  const out: StoredConfig = { ...incoming };
  for (const k of ["secret", "password"]) {
    if (!isSecretKey(kind, k)) continue;
    const v = incoming[k];
    if (v === undefined || v === SECRET_MASK) { if (stored[k] !== undefined) out[k] = stored[k]; else delete out[k]; }
    else if (v === "") delete out[k];
  }
  if (kind === "webhook" && incoming.headers && typeof incoming.headers === "object") {
    const was = (stored.headers ?? {}) as Record<string, string>;
    out.headers = Object.fromEntries(Object.entries(incoming.headers as Record<string, string>).map(([k, v]) => [k, v === SECRET_MASK ? (was[k] ?? "") : v]));
  }
  return out;
}

export function parseConnectionConfig(kind: ConnectionKind, config: unknown): { ok: true; config: StoredConfig } | { ok: false; errors: string[] } {
  const r = CONFIG_BY_KIND[kind].safeParse(config);
  return r.success ? { ok: true, config: r.data as StoredConfig } : { ok: false, errors: r.error.issues.map((i) => `config.${i.path.join(".")}: ${i.message}`.replace(/\.:/, ":")) };
}

// ---- conditions -----------------------------------------------------------------------------------------------------
const hhmm = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "Use HH:MM");
export const conditionSchema = z.discriminatedUnion("field", [
  z.object({ field: z.literal("camera"), in: z.array(uuid).min(1).max(100) }).strict(),
  z.object({ field: z.literal("label"), in: z.array(z.string().min(1).max(40)).min(1).max(40) }).strict(),
  z.object({ field: z.literal("severity"), at_least: z.enum(SEVERITY_ORDER) }).strict(),
  z.object({ field: z.literal("indicator"), triggered: z.union([z.literal("*"), z.string().regex(/^[a-z0-9_]{2,40}$/)]) }).strict(),
  z.object({ field: z.literal("classified_as"), is: z.enum(CLASSIFIED_AS) }).strict(),
  z.object({ field: z.literal("speed_kmh"), above: z.number().min(0).max(500) }).strict(),
  z.object({ field: z.literal("suspicious"), is: z.boolean() }).strict(),
  z.object({ field: z.literal("time"), between: z.tuple([hhmm, hhmm]), utc_offset_min: z.number().int().min(-840).max(840) }).strict(),
]);
export type Condition = z.infer<typeof conditionSchema>;

// ---- actions --------------------------------------------------------------------------------------------------------
const template = z.string().max(4000);
const address = z.number().int().min(0).max(65535);
export const actionSchema = z.union([
  z.object({ type: z.literal("webhook"), connection: uuid, body: template.optional() }).strict(),
  z.object({ type: z.literal("mqtt"), connection: uuid, topic: z.string().trim().min(1).max(200).regex(/^[^+#\s]+$/, "A topic to publish to has no spaces, + or #"), payload: template.optional(), retain: z.boolean().default(false) }).strict(),
  z.object({ type: z.literal("modbus"), connection: uuid, function: z.literal("write_coil"), address, hold_s: z.number().int().min(1).max(60) }).strict(),
  z.object({ type: z.literal("modbus"), connection: uuid, function: z.literal("write_register"), address, value: z.number().int().min(0).max(65535) }).strict(),
]);
export type Action = z.infer<typeof actionSchema>;

const liveNeedsConfirm = (v: { mode?: string; confirm_live?: boolean }) => v.mode !== "live" || v.confirm_live === true;
const liveMsg = { path: ["confirm_live"], message: "Going live needs confirm_live: true" };
// no defaults here: the patch schema is built from these, and a default would silently reset fields that were not sent
const automationFields = {
  name: z.string().trim().min(1).max(80),
  description: z.string().trim().max(300),
  enabled: z.boolean(),
  mode: z.enum(["simulate", "live"]),
  conditions: z.array(conditionSchema).max(20),
  actions: z.array(actionSchema).min(1, "Add at least one action").max(10),
  cooldown_s: z.number().int().min(0).max(86400),
  max_per_hour: z.number().int().min(1).max(1000),
  position: z.number().int().min(0).max(1000),
  confirm_live: z.boolean(),
};
/** Create: practice mode and switched off unless said otherwise; going live needs confirm_live. */
export const automationInput = z.object({
  ...automationFields,
  description: automationFields.description.default(""), enabled: automationFields.enabled.default(false), mode: automationFields.mode.default("simulate"),
  conditions: automationFields.conditions.default([]), cooldown_s: automationFields.cooldown_s.default(60), max_per_hour: automationFields.max_per_hour.default(30),
  position: automationFields.position.default(0), confirm_live: automationFields.confirm_live.optional(),
}).strict().refine(liveNeedsConfirm, liveMsg);
/** Update: only the fields that are sent change. */
export const automationPatch = z.object(automationFields).partial().strict()
  .refine((v) => Object.keys(v).length > 0, "Nothing to update")
  .refine(liveNeedsConfirm, liveMsg);

/** Every action must name an enabled connection of the matching kind. Returns messages. */
export function checkActionConnections(actions: Action[], connections: { id: string; kind: string; enabled: boolean; name?: string }[]): string[] {
  const errs: string[] = [];
  actions.forEach((a, i) => {
    const c = connections.find((x) => x.id === a.connection);
    if (!c) errs.push(`actions.${i}: that connection does not exist`);
    else if (c.kind !== a.type) errs.push(`actions.${i}: ${c.name ?? "that connection"} is a ${c.kind} connection, not ${a.type}`);
    else if (!c.enabled) errs.push(`actions.${i}: ${c.name ?? "that connection"} is switched off`);
  });
  return errs;
}

// ---- template names -------------------------------------------------------------------------------------------------
/** Names the worker replaces in `{{name}}` (docs/AUTOMATIONS.md), with the plain label shown in the "Insert detail" picker. */
export const TEMPLATE_NAMES: { name: string; label: string }[] = [
  { name: "event.label", label: "What was seen" }, { name: "event.severity", label: "How serious" }, { name: "event.title", label: "Event title" },
  { name: "event.code", label: "Event number" }, { name: "event.detected_at", label: "When it happened" }, { name: "event.speed_kmh", label: "Speed (km/h)" },
  { name: "event.suspicious", label: "Marked suspicious" }, { name: "event.classified_as", label: "What the AI says it is" },
  { name: "event.indicators", label: "Warning signs" }, { name: "event.id", label: "Event id" },
  { name: "camera.name", label: "Camera name" }, { name: "camera.code", label: "Camera code" }, { name: "camera.id", label: "Camera id" },
  { name: "incident.code", label: "Incident number" }, { name: "automation.name", label: "Alert name" }, { name: "mode", label: "Practice or live" },
];

// ---- plain-language description -------------------------------------------------------------------------------------
type Named = { id: string; code?: string; name: string };
const list = (xs: string[]) => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} or ${xs[xs.length - 1]}`);
export const LABEL_WORDS: Record<string, string> = { person: "a person", car: "a car", truck: "a truck", bus: "a bus", motorcycle: "a motorcycle", bicycle: "a bicycle" };
const SEV_WORD: Record<string, string> = { LOW: "low", MEDIUM: "medium", HIGH: "high", CRITICAL: "critical" };
const AI_WORD: Record<string, string> = { person: "a person", vehicle: "a vehicle", false_alarm: "not a real detection", other: "another kind of object" };

export function describeCondition(c: Condition, cameras: Named[] = [], rules: { key: string; title: string }[] = []): string {
  switch (c.field) {
    case "camera": return `it is on ${list(c.in.map((id) => cameras.find((s) => s.id === id)?.name ?? "a removed camera"))}`;
    case "label": return `it sees ${list(c.in.map((l) => LABEL_WORDS[l] ?? `a ${l}`))}`;
    case "severity": return `it is at least ${SEV_WORD[c.at_least]} priority`;
    case "indicator": return c.triggered === "*" ? "any warning sign appeared" : `the warning sign “${rules.find((r) => r.key === c.triggered)?.title ?? c.triggered}” appeared`;
    case "classified_as": return `the AI check says it is ${AI_WORD[c.is]}`;
    case "speed_kmh": return `it moves faster than ${c.above} km/h`;
    case "suspicious": return c.is ? "it is marked suspicious" : "it is not marked suspicious";
    case "time": return `the time is between ${c.between[0]} and ${c.between[1]}`;
  }
}

export function describeAction(a: Action, connections: { id: string; name: string }[] = []): string {
  const where = connections.find((c) => c.id === a.connection)?.name ?? "a removed connection";
  if (a.type === "webhook") return `send a message to ${where}`;
  if (a.type === "mqtt") return `publish a message on “${a.topic}” via ${where}`;
  return a.function === "write_coil" ? `switch output ${a.address} on for ${a.hold_s} s on ${where}` : `write the value ${a.value} to register ${a.address} on ${where}`;
}

/** “When it sees a person and it is at least high priority, then send a message to Plant.” */
export function describeAutomation(conds: Condition[], actions: Action[], cameras: Named[] = [], connections: { id: string; name: string }[] = [], rules: { key: string; title: string }[] = []): string {
  const when = conds.length ? conds.map((c) => describeCondition(c, cameras, rules)).join(" and ") : "any event happens";
  const then = actions.length ? actions.map((a) => describeAction(a, connections)).join(", then ") : "do nothing yet";
  return `When ${when}, then ${then}.`;
}

// ---- tests that wait for the analysis service -----------------------------------------------------------------------
export type RunStatus = "queued" | "running" | "sent" | "simulated" | "failed" | "skipped";
export type RunRow = { id: number; status: RunStatus; detail?: { reason?: string; actions?: { type: string; target?: string; ok: boolean; message?: string; would_send?: unknown; ms?: number }[] } };

/** Polls until the run leaves `queued` (every `intervalMs`, up to `timeoutMs`). `null` = nobody picked it up in time. */
export async function waitForRun(fetchRun: () => Promise<RunRow | undefined>, { intervalMs = 1000, timeoutMs = 15000 }: { intervalMs?: number; timeoutMs?: number } = {}, sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms))): Promise<RunRow | null> {
  let last: RunRow | undefined;
  for (let waited = 0; waited <= timeoutMs; waited += intervalMs) {
    last = await fetchRun();
    if (last && last.status !== "queued") return last;
    if (waited + intervalMs > timeoutMs) break;
    await sleep(intervalMs);
  }
  return null;
}
