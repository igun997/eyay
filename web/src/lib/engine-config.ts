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

export const engineConfigSchema = z
  .object({ detector: detectorSchema.nullish(), tracker: trackerSchema.nullish(), calibration: calibrationSchema.nullish() })
  .strict();

export type EngineConfig = {
  detector?: { nms_iou?: number | null; target_fps?: number | null; intra_threads?: number | null };
  tracker?: { iou_threshold?: number | null; max_age?: number | null; min_hits?: number | null };
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
  calibration: { method: "none", speed_limit_kmh: 50, speed_severity: "HIGH", show_geometry: true, line_axis: "x", smoothing: 0.4 },
};

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
    if (c.line1 == null || c.line2 == null) errs.push("calibration: method line needs both line1 and line2");
    else if (Math.abs(c.line1 - c.line2) < MIN_LINE_GAP) errs.push(`calibration: line1 and line2 must be at least ${MIN_LINE_GAP} apart`);
    if (c.distance_m == null) errs.push("calibration: method line needs distance_m, the measured real distance between the lines (it scales every speed)");
  }
  if (c.method === "homography") {
    if (!c.pixels || c.pixels.length !== 4) errs.push("calibration: method homography needs four pixel corners");
    if (!c.world_m || c.world_m.length !== 4) errs.push("calibration: method homography needs four world_m points in metres");
    else if (c.pixels && c.pixels.length === 4 && quadArea(c.world_m) < 0.01) errs.push("calibration: world_m corners enclose no area");
    if (c.pixels && c.pixels.length === 4 && quadArea(c.pixels) < 0.0005) errs.push("calibration: pixel corners enclose no area");
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

/** Strict parse of a config document: unknown keys and out-of-range values are rejected, null leaves dropped. */
export function parseConfig(input: unknown): ParseResult {
  const r = engineConfigSchema.safeParse(input);
  if (!r.success) return { ok: false, errors: r.error.issues.map((i) => `${i.path.join(".") || "config"}: ${i.message}`) };
  const config = compact(r.data as EngineConfig);
  const own = checkInternal(config);
  return own.length ? { ok: false, errors: own } : { ok: true, config };
}

/** Rules that hold inside one document regardless of inheritance. */
function checkInternal(cfg: EngineConfig): string[] {
  const c = cfg.calibration;
  if (c?.line1 != null && c?.line2 != null && Math.abs(c.line1 - c.line2) < MIN_LINE_GAP) return [`calibration: line1 and line2 must be at least ${MIN_LINE_GAP} apart`];
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

