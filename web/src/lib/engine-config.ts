// Engine configuration contract: docs/ENGINE_CONFIG.md. Pure (no server imports) so the dashboard form and the API
// validate with the very same ranges. A key that is absent or null inherits (per camera -> defaults -> worker built-in).
import { z } from "zod";

export const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const;
export const METHODS = ["none", "line", "homography"] as const;
export const MIN_LINE_GAP = 0.02;

const num = (min: number, max: number) => z.number().min(min).max(max).nullish();
const int = (min: number, max: number) => z.number().int().min(min).max(max).nullish();
const frac = z.number().min(0).max(1);
const point = z.tuple([frac, frac]);
const metres = z.tuple([z.number().min(-10000).max(10000), z.number().min(-10000).max(10000)]);

export const detectorSchema = z.object({ nms_iou: num(0.1, 0.9), target_fps: num(0.5, 15), intra_threads: int(0, 32) }).strict();
export const trackerSchema = z.object({ iou_threshold: num(0.05, 0.9), max_age: int(0, 60), min_hits: int(1, 10) }).strict();
export const calibrationSchema = z
  .object({
    method: z.enum(METHODS).nullish(),
    speed_limit_kmh: num(5, 300),
    speed_severity: z.enum(SEVERITIES).nullish(),
    show_geometry: z.boolean().nullish(),
    line_axis: z.enum(["x", "y"]).nullish(),
    line1: num(0, 1),
    line2: num(0, 1),
    distance_m: num(0.5, 1000),
    pixels: z.array(point).length(4).nullish(),
    world_m: z.array(metres).length(4).nullish(),
    smoothing: num(0, 1),
  })
  .strict();

export const eventsSchema = z.object({ new_track_gap_s: num(0, 300) }).strict();
/** Global only (one classifier loop serves every camera): accepted in the defaults, rejected in a camera's config. */
export const classifierSchema = z
  .object({ rules_enabled: z.boolean().nullish(), rule_frames: int(1, 6), rule_max_queue: int(0, 200), batch: z.boolean().nullish() })
  .strict();

export const engineConfigSchema = z
  .object({ detector: detectorSchema.nullish(), tracker: trackerSchema.nullish(), events: eventsSchema.nullish(), calibration: calibrationSchema.nullish(), classifier: classifierSchema.nullish() })
  .strict();

export type EngineConfig = {
  detector?: { nms_iou?: number | null; target_fps?: number | null; intra_threads?: number | null };
  tracker?: { iou_threshold?: number | null; max_age?: number | null; min_hits?: number | null };
  events?: { new_track_gap_s?: number | null };
  classifier?: { rules_enabled?: boolean | null; rule_frames?: number | null; rule_max_queue?: number | null; batch?: boolean | null };
  calibration?: {
    method?: (typeof METHODS)[number] | null; speed_limit_kmh?: number | null; speed_severity?: (typeof SEVERITIES)[number] | null;
    show_geometry?: boolean | null; line_axis?: "x" | "y" | null; line1?: number | null; line2?: number | null; distance_m?: number | null;
    pixels?: [number, number][] | null; world_m?: [number, number][] | null; smoothing?: number | null;
  };
};

/** What the worker uses when neither the camera nor the defaults set a key (target_fps / intra_threads fall back to the worker's env). */
export const BUILTIN: Required<{ [G in keyof EngineConfig]: NonNullable<EngineConfig[G]> }> = {
  detector: { nms_iou: 0.45, target_fps: 5, intra_threads: 0 },
  tracker: { iou_threshold: 0.3, max_age: 10, min_hits: 2 },
  events: { new_track_gap_s: 0 },
  // CLASSIFIER_* environment variables are the worker's fallback when a key is unset
  classifier: { rules_enabled: true, rule_frames: 1, rule_max_queue: 10, batch: false },
  calibration: { method: "none", speed_limit_kmh: 50, speed_severity: "HIGH", show_geometry: true, line_axis: "x", smoothing: 0.4 },
};

/** The plain name of each setting, used in form labels and error messages. */
export const FIELD_LABELS: Record<string, string> = {
  "detector.nms_iou": "Merge overlapping boxes", "detector.target_fps": "Checks per second", "detector.intra_threads": "Processing power",
  "tracker.iou_threshold": "How strictly to follow the same object", "tracker.max_age": "How long to remember an object that disappears", "tracker.min_hits": "How many looks before it counts",
  "events.new_track_gap_s": "Quiet-period exception for a new object",
  "classifier.rules_enabled": "Warning-sign questions", "classifier.rule_frames": "Follow-up photos to check", "classifier.rule_max_queue": "Skip questions when this many are waiting", "classifier.batch": "Ask all questions at once",
  "calibration.method": "Speed setup method", "calibration.speed_limit_kmh": "Speed limit", "calibration.speed_severity": "Over-limit warning severity", "calibration.show_geometry": "Show the lines on the live view",
  "calibration.line_axis": "Direction measured", "calibration.line1": "Line 1", "calibration.line2": "Line 2", "calibration.distance_m": "Real distance between the lines",
  "calibration.pixels": "Corners on the picture", "calibration.world_m": "Corners in metres", "calibration.smoothing": "Smoothing",
};
const GROUP_LABELS: Record<string, string> = { detector: "Camera AI", tracker: "Following objects", events: "Events", classifier: "Second AI check", calibration: "Speed setup" };
export const plainPath = (path: (string | number | symbol)[]) => FIELD_LABELS[path.slice(0, 2).join(".")] ?? (path.length ? GROUP_LABELS[String(path[0])] ?? "Settings" : "Settings");

type Issue = { path: (string | number | symbol)[]; code: string; message: string; minimum?: unknown; maximum?: unknown };
function plainIssue(i: Issue): string {
  const what = plainPath(i.path);
  if (i.code === "too_small") return `${what}: must be at least ${String(i.minimum)}`;
  if (i.code === "too_big") return `${what}: must be at most ${String(i.maximum)}`;
  if (i.code === "unrecognized_keys") return `${what}: that setting does not exist`;
  if (i.code === "invalid_type") return `${what}: that is not a valid value`;
  return `${what}: ${i.message}`;
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** Drop null / undefined leaves and groups that end up empty: only real overrides are stored. */
export function compact(cfg: EngineConfig | null | undefined): EngineConfig {
  const out: Record<string, Record<string, unknown>> = {};
  for (const [group, leaves] of Object.entries(cfg ?? {})) {
    if (!isObj(leaves)) continue;
    const kept = Object.fromEntries(Object.entries(leaves).filter(([, v]) => v !== null && v !== undefined));
    if (Object.keys(kept).length) out[group] = kept;
  }
  return out as EngineConfig;
}

/** `over` wins leaf by leaf; arrays (point lists) are replaced as a whole. */
export function mergeConfig(base: EngineConfig | null | undefined, over: EngineConfig | null | undefined): EngineConfig {
  const b = compact(base) as Record<string, Record<string, unknown>>;
  const o = compact(over) as Record<string, Record<string, unknown>>;
  const out: Record<string, Record<string, unknown>> = {};
  for (const g of new Set([...Object.keys(b), ...Object.keys(o)])) out[g] = { ...b[g], ...o[g] };
  return out as EngineConfig;
}

/** The value a field resolves to: camera override, else defaults, else the worker built-in (undefined = worker decides / unset). */
export function effective<G extends keyof EngineConfig>(group: G, key: string, defaults: EngineConfig | undefined, cam: EngineConfig | undefined): { value: unknown; from: "camera" | "defaults" | "builtin" | "unset" } {
  const pick = (c: EngineConfig | undefined) => (c?.[group] as Record<string, unknown> | undefined)?.[key];
  const camV = pick(cam), defV = pick(defaults), bi = (BUILTIN[group] as Record<string, unknown>)[key];
  if (camV !== undefined && camV !== null) return { value: camV, from: "camera" };
  if (defV !== undefined && defV !== null) return { value: defV, from: "defaults" };
  if (bi !== undefined) return { value: bi, from: "builtin" };
  return { value: undefined, from: "unset" };
}

/** Cross-field rules on the config a camera will actually run with (defaults merged with the camera's overrides). Returns messages. */
export function checkEffective(merged: EngineConfig): string[] {
  const c = { ...BUILTIN.calibration, ...(merged.calibration ?? {}) };
  const errs: string[] = [];
  if (c.method === "line") {
    if (c.line1 == null || c.line2 == null) errs.push("Speed setup: two lines need both line positions");
    else if (Math.abs(c.line1 - c.line2) < MIN_LINE_GAP) errs.push(`Speed setup: the two lines must be at least ${MIN_LINE_GAP} apart`);
    if (c.distance_m == null) errs.push("Speed setup: enter the real distance between the two lines, measured on site (it scales every speed)");
  }
  if (c.method === "homography") {
    if (!c.pixels || c.pixels.length !== 4) errs.push("Speed setup: an angled view needs four corners drawn on the picture");
    if (!c.world_m || c.world_m.length !== 4) errs.push("Speed setup: an angled view needs the same four corners in metres");
    else if (c.pixels && c.pixels.length === 4 && quadArea(c.world_m) < 0.01) errs.push("Speed setup: the four corners in metres enclose no area");
    if (c.pixels && c.pixels.length === 4 && quadArea(c.pixels) < 0.0005) errs.push("Speed setup: the four corners on the picture enclose no area");
  }
  return errs;
}

/** Shoelace area of a four-point polygon (absolute). */
export function quadArea(p: [number, number][]): number {
  let s = 0;
  for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length]; s += x1 * y2 - x2 * y1; }
  return Math.abs(s) / 2;
}

export type ParseResult = { ok: true; config: EngineConfig } | { ok: false; errors: string[] };

/**
 * Strict parse of a config document: unknown keys and out-of-range values are rejected, null leaves dropped.
 * `scope` "camera" (default) also rejects the global-only `classifier` section; "defaults" accepts it.
 */
export function parseConfig(input: unknown, scope: "camera" | "defaults" = "camera"): ParseResult {
  const r = engineConfigSchema.safeParse(input);
  if (!r.success) return { ok: false, errors: r.error.issues.map((i) => plainIssue(i as unknown as Issue)) };
  const config = compact(r.data as EngineConfig);
  if (scope === "camera" && config.classifier) return { ok: false, errors: ["Second AI check settings apply to every camera, so set them in the settings for all cameras, not per camera"] };
  const own = checkInternal(config);
  return own.length ? { ok: false, errors: own } : { ok: true, config };
}

/** Rules that hold inside one document regardless of inheritance. */
function checkInternal(cfg: EngineConfig): string[] {
  const c = cfg.calibration;
  if (c?.line1 != null && c?.line2 != null && Math.abs(c.line1 - c.line2) < MIN_LINE_GAP) return [`Speed setup: the two lines must be at least ${MIN_LINE_GAP} apart`];
  return [];
}

/** Only real overrides leave the editor: keys the camera already overrode, or that now differ from what it inherits. */
type Cal = NonNullable<EngineConfig["calibration"]>;
export function overrides(d: Cal, current: Cal, inherited: Cal): Cal {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(d)) {
    if (v === null || v === undefined) continue;
    const had = (current as Record<string, unknown>)[k] != null;
    if (had || JSON.stringify(v) !== JSON.stringify((inherited as Record<string, unknown>)[k])) out[k] = v;
  }
  return out as Cal;
}

