import { describe, expect, it } from "vitest";
import { BUILTIN, checkEffective, compact, effective, mergeConfig, overrides, parseConfig, quadArea } from "@/lib/engine-config";
import { formatSpeed } from "@/lib/format";
import { normaliseRule, ruleInput, ruleKey, ruleUpdate } from "@/lib/schemas";

describe("parseConfig ranges (docs/ENGINE_CONFIG.md)", () => {
  it.each([
    [{ detector: { nms_iou: 0.1 } }], [{ detector: { nms_iou: 0.9 } }], [{ detector: { target_fps: 15 } }], [{ detector: { intra_threads: 32 } }],
    [{ tracker: { iou_threshold: 0.05, max_age: 0, min_hits: 1 } }], [{ tracker: { iou_threshold: 0.9, max_age: 60, min_hits: 10 } }],
    [{ calibration: { speed_limit_kmh: 5 } }], [{ calibration: { speed_limit_kmh: 300 } }], [{ calibration: { smoothing: 1, distance_m: 1000 } }],
  ])("accepts the boundary values %j", (cfg) => expect(parseConfig(cfg).ok).toBe(true));

  it.each([
    [{ detector: { nms_iou: 0.09 } }], [{ detector: { nms_iou: 0.91 } }], [{ detector: { target_fps: 0.4 } }], [{ detector: { target_fps: 16 } }],
    [{ detector: { intra_threads: 33 } }], [{ detector: { intra_threads: 1.5 } }], [{ tracker: { iou_threshold: 0.04 } }], [{ tracker: { max_age: 61 } }],
    [{ tracker: { min_hits: 0 } }], [{ tracker: { min_hits: 11 } }], [{ calibration: { speed_limit_kmh: 4 } }], [{ calibration: { speed_limit_kmh: 301 } }],
    [{ calibration: { distance_m: 0.4 } }], [{ calibration: { line1: 1.1 } }], [{ calibration: { smoothing: -0.1 } }], [{ calibration: { method: "radar" } }],
    [{ calibration: { speed_severity: "URGENT" } }], [{ calibration: { line_axis: "z" } }],
  ])("rejects %j", (cfg) => expect(parseConfig(cfg).ok).toBe(false));

  it("rejects unknown groups and keys instead of storing them", () => {
    expect(parseConfig({ detector: { model: "x.onnx" } }).ok).toBe(false);
    expect(parseConfig({ extra: {} }).ok).toBe(false);
    expect(parseConfig("nope").ok).toBe(false);
  });

  it("drops null leaves and empty groups: only real overrides are stored", () => {
    const r = parseConfig({ detector: { nms_iou: null, target_fps: 3 }, tracker: { max_age: null }, calibration: null });
    expect(r).toEqual({ ok: true, config: { detector: { target_fps: 3 } } });
  });

  it("requires four points for the quad and keeps points inside the frame", () => {
    expect(parseConfig({ calibration: { pixels: [[0, 0], [0, 1], [1, 1]] } }).ok).toBe(false);
    expect(parseConfig({ calibration: { pixels: [[0, 0], [0, 1], [1, 1], [1.2, 0]] } }).ok).toBe(false);
    expect(parseConfig({ calibration: { world_m: [[0, 0], [0, 30], [7, 30], [7, 0]] } }).ok).toBe(true);
  });

  it("keeps the two lines at least 0.02 apart", () => {
    expect(parseConfig({ calibration: { line1: 0.5, line2: 0.51 } }).ok).toBe(false);
    expect(parseConfig({ calibration: { line1: 0.5, line2: 0.52 } }).ok).toBe(true);
  });
});

describe("merge and inheritance", () => {
  it("the camera overrides the defaults leaf by leaf, arrays are replaced whole", () => {
    const merged = mergeConfig(
      { detector: { nms_iou: 0.5, target_fps: 4 }, calibration: { pixels: [[0, 0], [0, 1], [1, 1], [1, 0]] } },
      { detector: { target_fps: 2 }, calibration: { pixels: [[0.1, 0.1], [0.1, 0.9], [0.9, 0.9], [0.9, 0.1]] } },
    );
    expect(merged.detector).toEqual({ nms_iou: 0.5, target_fps: 2 });
    expect(merged.calibration?.pixels?.[0]).toEqual([0.1, 0.1]);
  });
  it("reports where a value comes from: camera, defaults, built-in or unset", () => {
    const defaults = { tracker: { max_age: 20 } };
    const cam = { tracker: { min_hits: 4 } };
    expect(effective("tracker", "min_hits", defaults, cam)).toEqual({ value: 4, from: "camera" });
    expect(effective("tracker", "max_age", defaults, cam)).toEqual({ value: 20, from: "defaults" });
    expect(effective("tracker", "iou_threshold", defaults, cam)).toEqual({ value: BUILTIN.tracker.iou_threshold, from: "builtin" });
    expect(effective("calibration", "distance_m", defaults, cam)).toEqual({ value: undefined, from: "unset" });
  });
  it("compact removes null and undefined only", () => {
    expect(compact({ detector: { nms_iou: null, intra_threads: 0 } })).toEqual({ detector: { intra_threads: 0 } });
    expect(compact(null)).toEqual({});
  });
});

describe("checkEffective: the config a camera really runs with", () => {
  it("a line needs both lines and a measured distance (it scales every speed)", () => {
    expect(checkEffective({ calibration: { method: "line" } }).length).toBeGreaterThan(0);
    expect(checkEffective({ calibration: { method: "line", line1: 0.4, line2: 0.7 } }).join()).toMatch(/distance_m/);
    expect(checkEffective({ calibration: { method: "line", line1: 0.4, line2: 0.7, distance_m: 12 } })).toEqual([]);
  });
  it("inherited geometry counts: a camera may only switch the method on", () => {
    const merged = mergeConfig({ calibration: { line1: 0.3, line2: 0.6, distance_m: 15 } }, { calibration: { method: "line" } });
    expect(checkEffective(merged)).toEqual([]);
  });
  it("a homography needs four pixel corners and four metre points that enclose an area", () => {
    expect(checkEffective({ calibration: { method: "homography" } }).length).toBe(2);
    const quad: [number, number][] = [[0.1, 0.9], [0.1, 0.5], [0.9, 0.5], [0.9, 0.9]];
    expect(checkEffective({ calibration: { method: "homography", pixels: quad, world_m: [[0, 0], [0, 30], [7, 30], [7, 0]] } })).toEqual([]);
    expect(checkEffective({ calibration: { method: "homography", pixels: quad, world_m: [[0, 0], [1, 1], [2, 2], [3, 3]] } }).join()).toMatch(/enclose no area/);
  });
  it("method none and an empty config are always fine", () => {
    expect(checkEffective({})).toEqual([]);
    expect(checkEffective({ calibration: { method: "none" } })).toEqual([]);
  });
  it("quadArea is the polygon area", () => expect(quadArea([[0, 0], [0, 2], [3, 2], [3, 0]])).toBe(6));
});

describe("calibration editor output", () => {
  it("returns only keys the camera overrode or that now differ from what it inherits", () => {
    const inherited = { method: "none" as const, speed_limit_kmh: 50, line_axis: "x" as const };
    const out = overrides({ method: "line", speed_limit_kmh: 50, line_axis: "x", line1: 0.4, line2: 0.7, distance_m: 12 }, {}, inherited);
    expect(out).toEqual({ method: "line", line1: 0.4, line2: 0.7, distance_m: 12 });
    expect(overrides({ speed_limit_kmh: 50 }, { speed_limit_kmh: 50 }, inherited)).toEqual({ speed_limit_kmh: 50 });
  });
  it("formats the speed rounded to km/h", () => expect(formatSpeed(61.6)).toBe("62 km/h"));
});

describe("behaviour rule input", () => {
  const base = { title: "Knife", description: "A person holds a knife.", kind: "classifier", question: "Is the person holding a knife?", applies_to: ["person"], severity: "CRITICAL" };
  it("accepts a classifier rule and applies the defaults", () => {
    const v = ruleInput.parse({ ...base, key: "knife" });
    expect(v).toMatchObject({ threshold: 0.75, opens_incident: true, enabled: true, position: 0, config: {} });
  });
  it.each(["a", "Knife", "knife-1", "has space", "x".repeat(41), ""])("rejects the key %j", (k) => expect(ruleKey.safeParse(k).success).toBe(false));
  it("a classifier rule needs a question, an indication rule needs the kind it reacts to", () => {
    expect(ruleInput.safeParse({ ...base, key: "knife", question: null }).success).toBe(false);
    expect(ruleInput.safeParse({ ...base, key: "dwell", kind: "indication", question: null }).success).toBe(false);
    expect(ruleInput.safeParse({ ...base, key: "dwell", kind: "indication", question: null, config: { indication_kind: "lingering", min_burst_ms: 5000 } }).success).toBe(true);
  });
  it("threshold is in (0, 1]", () => {
    for (const t of [0, -0.1, 1.01]) expect(ruleInput.safeParse({ ...base, key: "knife", threshold: t }).success).toBe(false);
    expect(ruleInput.safeParse({ ...base, key: "knife", threshold: 1 }).success).toBe(true);
  });
  it("needs a category, a known severity and a known indication kind", () => {
    expect(ruleInput.safeParse({ ...base, key: "knife", applies_to: [] }).success).toBe(false);
    expect(ruleInput.safeParse({ ...base, key: "knife", severity: "URGENT" }).success).toBe(false);
    expect(ruleInput.safeParse({ ...base, key: "d", kind: "indication", config: { indication_kind: "running" } }).success).toBe(false);
  });
  it("update ignores the key and a switched kind leaves no stale columns", () => {
    const v = normaliseRule(ruleUpdate.parse({ ...base, kind: "indication", config: { indication_kind: "moving", min_burst_ms: 2000 } }));
    expect(v.question).toBeNull();
    expect(normaliseRule(ruleUpdate.parse(base)).config).toEqual({});
  });
});
