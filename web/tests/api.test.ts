import { beforeEach, describe, expect, it, vi } from "vitest";

// the handlers are exercised for real; only the session and the database are replaced
const session = vi.hoisted(() => ({ role: "admin" as "admin" | "security" | null }));
vi.mock("@/lib/server/auth", () => {
  class HttpError extends Error { constructor(public status: number, message: string) { super(message); } }
  return {
    HttpError,
    requireSession: async (roles?: string[]) => {
      if (!session.role) throw new HttpError(401, "Not signed in");
      if (roles && !roles.includes(session.role)) throw new HttpError(403, "Not allowed for your role");
      return { uid: "00000000-0000-0000-0000-000000000001", email: "a@b.c", name: "A", role: session.role };
    },
  };
});

const db = vi.hoisted(() => ({
  defaults: {} as Record<string, unknown>,
  saved: [] as unknown[],
  sourceConfig: null as unknown,
  sourceExists: true,
  rules: new Map<string, Record<string, unknown>>(),
  audit: [] as unknown[][],
  duplicate: false,
  categories: new Map<string, Record<string, unknown>>([["person", { label: "person", title: "Person" }]]),
}));
vi.mock("@/lib/server/db", () => ({
  query: async (text: string, params: unknown[] = []) => { if (/audit_log/.test(text)) db.audit.push([text, ...params]); return []; },
  one: async () => null,
}));
vi.mock("@/lib/server/queries", () => ({
  getEngineDefaults: async () => db.defaults,
  setEngineDefaults: async (cfg: unknown) => { db.saved.push(cfg); },
  setSourceConfig: async (_id: string, cfg: unknown) => { if (!db.sourceExists) return null; db.sourceConfig = cfg; return { code: "CAM-01" }; },
  listBehaviorRules: async () => [...db.rules.values()],
  listCategories: async () => ["person", "car", "truck"].map((label) => ({ label })),
  insertBehaviorRule: async (key: string, r: Record<string, unknown>) => {
    if (db.duplicate || db.rules.has(key)) throw Object.assign(new Error("dup"), { code: "23505" });
    const row = { key, ...r }; db.rules.set(key, row); return row;
  },
  updateBehaviorRule: async (key: string, r: Record<string, unknown>) => { if (!db.rules.has(key)) return null; const row = { key, ...r }; db.rules.set(key, row); return row; },
  deleteBehaviorRule: async (key: string) => db.rules.delete(key),
  updateCategory: async (label: string, patch: Record<string, unknown>) => { const c = db.categories.get(label); if (!c) return null; const row = { ...c, ...patch }; db.categories.set(label, row); return row; },
}));

import { GET as getDefaults, PUT as putDefaults } from "@/app/api/settings/engine/route";
import { PUT as putSourceConfig } from "@/app/api/sources/[id]/config/route";
import { GET as listRules, POST as createRule } from "@/app/api/rules/route";
import { PUT as updateRule, DELETE as deleteRule } from "@/app/api/rules/[key]/route";
import { PUT as updateCategory } from "@/app/api/categories/[label]/route";

const json = (body: unknown, method = "PUT") =>
  new Request("http://x/api", method === "GET" || method === "DELETE" ? { method } : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const ID = "9ea0bb5d-faa9-4b16-a51d-d9579ba64b39";
const ctx = <P,>(p: P) => ({ params: Promise.resolve(p) });
const rule = { key: "knife", title: "Knife", description: "A person holds a knife.", kind: "classifier", question: "Is the person holding a knife?", applies_to: ["person"], severity: "CRITICAL" };

beforeEach(() => {
  session.role = "admin"; db.defaults = {}; db.saved = []; db.sourceConfig = null; db.sourceExists = true; db.rules.clear(); db.audit = []; db.duplicate = false; db.categories = new Map([["person", { label: "person", title: "Person" }]]);
});

describe("GET/PUT /api/settings/engine", () => {
  it("is admin only", async () => {
    session.role = "security";
    expect((await getDefaults(json(null, "GET"), ctx({}))).status).toBe(403);
    expect((await putDefaults(json({}), ctx({}))).status).toBe(403);
    session.role = null;
    expect((await getDefaults(json(null, "GET"), ctx({}))).status).toBe(401);
  });
  it("returns the defaults and the built-in values", async () => {
    db.defaults = { tracker: { max_age: 20 } };
    const body = await (await getDefaults(json(null, "GET"), ctx({}))).json();
    expect(body.defaults).toEqual({ tracker: { max_age: 20 } });
    expect(body.builtin.tracker.min_hits).toBe(2);
  });
  it("stores only real overrides and writes the audit log", async () => {
    const res = await putDefaults(json({ detector: { nms_iou: 0.5, target_fps: null }, tracker: {} }), ctx({}));
    expect(res.status).toBe(200);
    expect(db.saved).toEqual([{ detector: { nms_iou: 0.5 } }]);
    expect(db.audit[0][0]).toMatch(/engine\.defaults\.update/);
  });
  it("accepts the global classifier tuning and the new-track gap, stored as sent", async () => {
    const cfg = { events: { new_track_gap_s: 8 }, classifier: { rules_enabled: true, rule_frames: 2, rule_max_queue: 20, batch: false } };
    expect((await putDefaults(json(cfg), ctx({}))).status).toBe(200);
    expect(db.saved).toEqual([cfg]);
  });
  it("rejects out-of-range values and unknown keys with 400, saving nothing", async () => {
    for (const bad of [{ detector: { nms_iou: 2 } }, { detector: { model: "x" } }, { calibration: { method: "line" } }]) {
      const res = await putDefaults(json(bad), ctx({}));
      expect(res.status).toBe(400);
    }
    expect(db.saved).toEqual([]);
  });
});

describe("PUT /api/sources/[id]/config", () => {
  it("is admin only", async () => {
    session.role = "security";
    expect((await putSourceConfig(json({}), ctx({ id: ID }))).status).toBe(403);
  });
  it("saves overrides and reports that the worker applies them within a poll", async () => {
    const res = await putSourceConfig(json({ tracker: { min_hits: 3 } }), ctx({ id: ID }));
    expect(res.status).toBe(200);
    expect((await res.json()).appliedWithinSeconds).toBe(5);
    expect(db.sourceConfig).toEqual({ tracker: { min_hits: 3 } });
    expect(db.audit[0][0]).toMatch(/source\.config/);
    expect(db.audit[0][2]).toBe("CAM-01");
  });
  it("accepts events.new_track_gap_s per camera but rejects the global-only classifier section", async () => {
    expect((await putSourceConfig(json({ events: { new_track_gap_s: 8 } }), ctx({ id: ID }))).status).toBe(200);
    expect(db.sourceConfig).toEqual({ events: { new_track_gap_s: 8 } });
    const res = await putSourceConfig(json({ classifier: { batch: true } }), ctx({ id: ID }));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toMatch(/every camera/);
    expect(db.sourceConfig).toEqual({ events: { new_track_gap_s: 8 } });
    expect((await putSourceConfig(json({ events: { new_track_gap_s: 301 } }), ctx({ id: ID }))).status).toBe(400);
  });
  it("an empty body clears every override (inherit everything)", async () => {
    expect((await putSourceConfig(json({}), ctx({ id: ID }))).status).toBe(200);
    expect(db.sourceConfig).toEqual({});
  });
  it("validates the config the camera will run with, defaults included", async () => {
    expect((await putSourceConfig(json({ calibration: { method: "line", line1: 0.4, line2: 0.7 } }), ctx({ id: ID }))).status).toBe(400); // no distance
    db.defaults = { calibration: { distance_m: 12 } };
    expect((await putSourceConfig(json({ calibration: { method: "line", line1: 0.4, line2: 0.7 } }), ctx({ id: ID }))).status).toBe(200); // inherits it
  });
  it("404 for a missing source, 400 for a malformed id or body", async () => {
    db.sourceExists = false;
    expect((await putSourceConfig(json({}), ctx({ id: ID }))).status).toBe(404);
    expect((await putSourceConfig(json({}), ctx({ id: "nope" }))).status).toBe(400);
    expect((await putSourceConfig(json({ tracker: { min_hits: 99 } }), ctx({ id: ID }))).status).toBe(400);
  });
});

describe("behaviour rules API", () => {
  it("anyone signed in can read, only admins can write", async () => {
    session.role = "security";
    expect((await listRules(json(null, "GET"), ctx({}))).status).toBe(200);
    expect((await createRule(json(rule, "POST"), ctx({}))).status).toBe(403);
    expect((await updateRule(json(rule), ctx({ key: "knife" }))).status).toBe(403);
    expect((await deleteRule(json(null, "DELETE"), ctx({ key: "knife" }))).status).toBe(403);
  });
  it("creates a rule, applying the defaults, and audits it", async () => {
    const res = await createRule(json(rule, "POST"), ctx({}));
    expect(res.status).toBe(200);
    expect(db.rules.get("knife")).toMatchObject({ threshold: 0.75, opens_incident: true, enabled: true, config: {} });
    expect(db.audit[0][0]).toMatch(/rule\.create/);
    expect(db.audit[0][2]).toBe("knife");
  });
  it("rejects bad input and unknown categories with 400", async () => {
    expect((await createRule(json({ ...rule, key: "Bad Key" }, "POST"), ctx({}))).status).toBe(400);
    expect((await createRule(json({ ...rule, question: null }, "POST"), ctx({}))).status).toBe(400);
    expect((await createRule(json({ ...rule, threshold: 0 }, "POST"), ctx({}))).status).toBe(400);
    const res = await createRule(json({ ...rule, applies_to: ["person", "dragon"] }, "POST"), ctx({}));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toMatch(/dragon/);
    expect(db.rules.size).toBe(0);
  });
  it("a duplicate key is a 409", async () => {
    await createRule(json(rule, "POST"), ctx({}));
    expect((await createRule(json(rule, "POST"), ctx({}))).status).toBe(409);
  });
  it("updates by key (the key itself never changes) and clears columns of the other kind", async () => {
    await createRule(json(rule, "POST"), ctx({}));
    const { key: _k, ...rest } = rule; void _k;
    const res = await updateRule(json({ ...rest, kind: "indication", question: "ignored?", config: { indication_kind: "lingering", min_burst_ms: 4000 } }), ctx({ key: "knife" }));
    expect(res.status).toBe(200);
    expect(db.rules.get("knife")).toMatchObject({ key: "knife", kind: "indication", question: null, config: { indication_kind: "lingering", min_burst_ms: 4000 } });
    expect((await updateRule(json(rest), ctx({ key: "missing" }))).status).toBe(404);
  });
  it("deletes by key, 404 when it is gone", async () => {
    await createRule(json(rule, "POST"), ctx({}));
    expect((await deleteRule(json(null, "DELETE"), ctx({ key: "knife" }))).status).toBe(200);
    expect((await deleteRule(json(null, "DELETE"), ctx({ key: "knife" }))).status).toBe(404);
    expect((await deleteRule(json(null, "DELETE"), ctx({ key: "BAD KEY" }))).status).toBe(400);
  });
});

describe("PUT /api/categories/[label]", () => {
  const patch = { title: "People", severity: "CRITICAL", cooldown_s: 20, burst_s: 8, burst_frames: 5, opens_incident: false, enabled: true };
  it("is admin only", async () => {
    session.role = "security";
    expect((await updateCategory(json(patch), ctx({ label: "person" }))).status).toBe(403);
    session.role = null;
    expect((await updateCategory(json(patch), ctx({ label: "person" }))).status).toBe(401);
  });
  it("updates the category and writes the audit log", async () => {
    const res = await updateCategory(json(patch), ctx({ label: "person" }));
    expect(res.status).toBe(200);
    expect(db.categories.get("person")).toMatchObject(patch);
    expect(db.audit[0][0]).toMatch(/category\.update/);
    expect(db.audit[0][2]).toBe("person");
  });
  it("accepts a partial update and a label with a space", async () => {
    db.categories.set("traffic light", { label: "traffic light" });
    expect((await updateCategory(json({ enabled: false }), ctx({ label: "traffic%20light" }))).status).toBe(200);
    expect(db.categories.get("traffic light")).toMatchObject({ enabled: false });
  });
  it("rejects out-of-range values, unknown keys, empty bodies and bad labels with 400", async () => {
    for (const bad of [{ cooldown_s: 86401 }, { burst_s: 61 }, { burst_frames: 13 }, { burst_frames: 0 }, { severity: "URGENT" }, { title: "" }, { label: "x" }, {}]) {
      expect((await updateCategory(json(bad), ctx({ label: "person" }))).status).toBe(400);
    }
    expect((await updateCategory(json(patch), ctx({ label: "Bad Label" }))).status).toBe(400);
    expect(db.categories.get("person")).toEqual({ label: "person", title: "Person" });
  });
  it("404 for an unknown category", async () => {
    expect((await updateCategory(json(patch), ctx({ label: "dragon" }))).status).toBe(404);
  });
});
