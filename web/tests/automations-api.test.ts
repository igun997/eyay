import { beforeEach, describe, expect, it, vi } from "vitest";

const session = vi.hoisted(() => ({ role: "admin" as "admin" | "security" | null }));
vi.mock("@/lib/server/auth", () => {
  class HttpError extends Error { constructor(public status: number, message: string, public params?: Record<string, string | number>) { super(message); } }
  return {
    HttpError,
    requireSession: async (roles?: string[]) => {
      if (!session.role) throw new HttpError(401, "err.notSignedIn");
      if (roles && !roles.includes(session.role)) throw new HttpError(403, "err.notAllowedForYourRole");
      return { uid: "00000000-0000-0000-0000-000000000001", email: "a@b.c", name: "A", role: session.role };
    },
  };
});

const ID = "9ea0bb5d-faa9-4b16-a51d-d9579ba64b39";
const CID = "3bc611c7-0cdd-4f3a-9a2e-0b6f5f7f1b10";
const state = vi.hoisted(() => ({
  connections: new Map<string, Record<string, any>>(),
  automations: new Map<string, Record<string, any>>(),
  queued: [] as Record<string, unknown>[],
  audit: [] as unknown[][],
  dupName: false,
  next: 1,
}));
vi.mock("@/lib/server/db", () => ({
  query: async (text: string, params: unknown[] = []) => { if (/audit_log/.test(text)) state.audit.push([text, ...params]); return []; },
  one: async () => null,
}));
vi.mock("@/lib/server/automations", () => ({
  listConnections: async () => [...state.connections.values()],
  getConnection: async (id: string) => state.connections.get(id) ?? null,
  insertConnection: async (name: string, kind: string, config: unknown, enabled: boolean) => {
    if (state.dupName) throw Object.assign(new Error("dup"), { code: "23505" });
    const row = { id: CID, name, kind, config, enabled, updated_at: "now" }; state.connections.set(CID, row); return row;
  },
  updateConnection: async (id: string, p: Record<string, unknown>) => { const c = state.connections.get(id); if (!c) return null; const row = { ...c, ...p }; state.connections.set(id, row); return row; },
  deleteConnection: async (id: string) => { const c = state.connections.get(id); state.connections.delete(id); return c ? { name: c.name } : null; },
  automationsUsingConnection: async (id: string) => [...state.automations.values()].filter((a) => a.actions.some((x: any) => x.connection === id)).map((a) => ({ id: a.id, name: a.name })),
  listAutomations: async () => [...state.automations.values()],
  getAutomation: async (id: string) => state.automations.get(id) ?? null,
  insertAutomation: async (a: Record<string, unknown>) => { const row = { id: ID, ...a, updated_at: "now" }; state.automations.set(ID, row); return row; },
  updateAutomation: async (id: string, p: Record<string, unknown>) => { const a = state.automations.get(id); if (!a) return null; const row = { ...a, ...p }; state.automations.set(id, row); return row; },
  deleteAutomation: async (id: string) => { const a = state.automations.get(id); state.automations.delete(id); return a ? { name: a.name } : null; },
  queueRun: async (r: Record<string, unknown>) => { state.queued.push(r); return { id: String(state.next++) }; },
  clampLimit: (raw: string | null, def: number, max = 500) => { const n = Number(raw); return Number.isInteger(n) && n > 0 ? Math.min(n, max) : def; },
  listRuns: async (limit: number) => [{ id: 1, limit }],
  listAutomationRuns: async (id: string, limit: number) => [{ id: 2, automation_id: id, limit }],
}));

import { GET as listConns, POST as createConn } from "@/app/api/connections/route";
import { PUT as updateConn, DELETE as deleteConn } from "@/app/api/connections/[id]/route";
import { POST as testConn } from "@/app/api/connections/[id]/test/route";
import { GET as listAutos, POST as createAuto } from "@/app/api/automations/route";
import { PUT as updateAuto, DELETE as deleteAuto } from "@/app/api/automations/[id]/route";
import { POST as previewAuto } from "@/app/api/automations/[id]/preview/route";
import { POST as testAuto } from "@/app/api/automations/[id]/test/route";
import { GET as autoRuns } from "@/app/api/automations/[id]/runs/route";
import { GET as allRuns } from "@/app/api/automation-runs/route";

const EN = { cookie: "eyeai_lang=en" }; // the handlers answer in the language of the cookie; the UI default is Indonesian
const req = (body: unknown, method = "POST", url = "http://x/api") =>
  new Request(url, method === "GET" || method === "DELETE" ? { method, headers: EN } : { method, headers: { "Content-Type": "application/json", ...EN }, body: JSON.stringify(body) });
const ctx = <P,>(p: P) => ({ params: Promise.resolve(p) });
const webhook = { name: "Control room", kind: "webhook", config: { url: "https://h.example/x", secret: "topsecret", headers: { "X-Api-Key": "abc" } } };

beforeEach(() => {
  session.role = "admin"; state.connections.clear(); state.automations.clear(); state.queued = []; state.audit = []; state.dupName = false; state.next = 1;
});
const addConn = async (extra: Record<string, unknown> = {}) => { expect((await createConn(req({ ...webhook, ...extra }), ctx({}))).status).toBe(200); };

describe("connections API", () => {
  it("anyone signed in reads, only admins write, nobody anonymous reads", async () => {
    session.role = "security";
    expect((await listConns(req(null, "GET"), ctx({}))).status).toBe(200);
    expect((await createConn(req(webhook), ctx({}))).status).toBe(403);
    expect((await updateConn(req({ enabled: false }, "PUT"), ctx({ id: CID }))).status).toBe(403);
    expect((await deleteConn(req(null, "DELETE"), ctx({ id: CID }))).status).toBe(403);
    expect((await testConn(req(null), ctx({ id: CID }))).status).toBe(403);
    session.role = null;
    expect((await listConns(req(null, "GET"), ctx({}))).status).toBe(401);
  });
  it("never returns a secret: create, list and update all show ********", async () => {
    const res = await createConn(req(webhook), ctx({}));
    const created = await res.json();
    expect(created.config.secret).toBe("********");
    expect(created.config.headers["X-Api-Key"]).toBe("********");
    const listed = JSON.stringify(await (await listConns(req(null, "GET"), ctx({}))).json());
    expect(listed).not.toMatch(/topsecret|abc/);
    expect(state.connections.get(CID)!.config.secret).toBe("topsecret"); // stored as entered
    const upd = await updateConn(req({ name: "Renamed", config: { url: "https://h.example/y", secret: "********", headers: { "X-Api-Key": "********" } } }, "PUT"), ctx({ id: CID }));
    expect(upd.status).toBe(200);
    expect(state.connections.get(CID)!.config).toMatchObject({ url: "https://h.example/y", secret: "topsecret", headers: { "X-Api-Key": "abc" } });
    expect(JSON.stringify(await upd.json())).not.toMatch(/topsecret|abc/);
  });
  it("replaces or clears a secret only when asked", async () => {
    await addConn();
    await updateConn(req({ config: { url: "https://h.example/x", secret: "newer" } }, "PUT"), ctx({ id: CID }));
    expect(state.connections.get(CID)!.config.secret).toBe("newer");
    await updateConn(req({ config: { url: "https://h.example/x", secret: "" } }, "PUT"), ctx({ id: CID }));
    expect(state.connections.get(CID)!.config).not.toHaveProperty("secret");
  });
  it("validates the config of its kind, rejects unknown keys, and a duplicate name is a 409", async () => {
    expect((await createConn(req({ ...webhook, config: { url: "ftp://x" } }), ctx({}))).status).toBe(400);
    expect((await createConn(req({ name: "p", kind: "modbus", config: { host: "plc", unit_id: 999 } }), ctx({}))).status).toBe(400);
    expect((await createConn(req({ ...webhook, nope: 1 }), ctx({}))).status).toBe(400);
    state.dupName = true;
    const res = await createConn(req(webhook), ctx({}));
    expect(res.status).toBe(409);
    expect((await res.json()).error).toMatch(/already exists/);
  });
  it("writes the audit log without the secret", async () => {
    await addConn();
    expect(state.audit[0][0]).toMatch(/connection\.create/);
    expect(JSON.stringify(state.audit)).not.toMatch(/topsecret/);
  });
  it("404 for a missing connection, 400 for a bad id", async () => {
    expect((await updateConn(req({ enabled: false }, "PUT"), ctx({ id: CID }))).status).toBe(404);
    expect((await updateConn(req({ enabled: false }, "PUT"), ctx({ id: "nope" }))).status).toBe(400);
    expect((await deleteConn(req(null, "DELETE"), ctx({ id: CID }))).status).toBe(404);
    expect((await testConn(req(null), ctx({ id: CID }))).status).toBe(404);
  });
  it("refuses to delete a connection an alert still uses (409), then deletes it", async () => {
    await addConn();
    state.automations.set(ID, { id: ID, name: "Night person", actions: [{ type: "webhook", connection: CID }] });
    const res = await deleteConn(req(null, "DELETE"), ctx({ id: CID }));
    expect(res.status).toBe(409);
    expect((await res.json()).error).toMatch(/Night person/);
    state.automations.clear();
    expect((await deleteConn(req(null, "DELETE"), ctx({ id: CID }))).status).toBe(200);
  });
  it("a connection check is queued for the analysis service, not run here", async () => {
    await addConn();
    const res = await testConn(req(null), ctx({ id: CID }));
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ ok: true, status: "queued", run_id: 1 });
    expect(state.queued).toEqual([{ automationId: null, connectionId: CID, mode: "test_send", userId: "00000000-0000-0000-0000-000000000001" }]);
  });
});

const alert = { name: "Night person", conditions: [{ field: "label", in: ["person"] }], actions: [{ type: "webhook", connection: CID }] };

describe("automations API", () => {
  beforeEach(async () => { await addConn(); });
  it("anyone signed in reads, only admins write", async () => {
    session.role = "security";
    expect((await listAutos(req(null, "GET"), ctx({}))).status).toBe(200);
    expect((await createAuto(req(alert), ctx({}))).status).toBe(403);
    expect((await updateAuto(req({ enabled: true }, "PUT"), ctx({ id: ID }))).status).toBe(403);
    expect((await deleteAuto(req(null, "DELETE"), ctx({ id: ID }))).status).toBe(403);
    expect((await previewAuto(req(null), ctx({ id: ID }))).status).toBe(403);
    expect((await testAuto(req(null), ctx({ id: ID }))).status).toBe(403);
    expect((await autoRuns(req(null, "GET"), ctx({ id: ID }))).status).toBe(200);
    expect((await allRuns(req(null, "GET"), ctx({}))).status).toBe(200);
  });
  it("creates in practice mode and switched off", async () => {
    const res = await createAuto(req(alert), ctx({}));
    expect(res.status).toBe(200);
    expect(state.automations.get(ID)).toMatchObject({ mode: "simulate", enabled: false, cooldown_s: 60, max_per_hour: 30 });
    expect(state.audit.at(-1)![0]).toMatch(/automation\.create/);
  });
  it("going live needs confirm_live, on create and on update", async () => {
    expect((await createAuto(req({ ...alert, mode: "live" }), ctx({}))).status).toBe(400);
    expect((await createAuto(req({ ...alert, mode: "live", confirm_live: true }), ctx({}))).status).toBe(200);
    state.automations.get(ID)!.mode = "simulate";
    expect((await updateAuto(req({ mode: "live" }, "PUT"), ctx({ id: ID }))).status).toBe(400);
    expect(state.automations.get(ID)!.mode).toBe("simulate");
    expect((await updateAuto(req({ mode: "live", confirm_live: true }, "PUT"), ctx({ id: ID }))).status).toBe(200);
    expect(state.automations.get(ID)!.mode).toBe("live");
    expect(state.audit.at(-1)![2]).toBe("automation.live");
    expect(state.audit.at(-1)![4]).toContain("\"confirm_live\":true");
  });
  it("an update changes only what it sends", async () => {
    await createAuto(req({ ...alert, cooldown_s: 120, description: "keep me" }), ctx({}));
    await updateAuto(req({ enabled: true }, "PUT"), ctx({ id: ID }));
    expect(state.automations.get(ID)).toMatchObject({ enabled: true, cooldown_s: 120, description: "keep me", mode: "simulate" });
  });
  it("every action must use an enabled connection of the right kind", async () => {
    expect((await createAuto(req({ ...alert, actions: [{ type: "mqtt", connection: CID, topic: "t" }] }), ctx({}))).status).toBe(400);
    expect((await createAuto(req({ ...alert, actions: [{ type: "webhook", connection: ID }] }), ctx({}))).status).toBe(400);
    state.connections.get(CID)!.enabled = false;
    const res = await createAuto(req(alert), ctx({}));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toMatch(/switched off/);
  });
  it("rejects invalid conditions, actions and limits", async () => {
    for (const bad of [
      { conditions: [{ field: "severity", at_least: "URGENT" }] }, { actions: [] }, { cooldown_s: 86401 }, { max_per_hour: 0 }, { name: "" }, { surprise: 1 },
      { actions: [{ type: "modbus", connection: CID, function: "write_coil", address: 0, hold_s: 61 }] },
    ]) expect((await createAuto(req({ ...alert, ...bad }), ctx({}))).status).toBe(400);
    expect(state.automations.size).toBe(0);
  });
  it("404 for a missing alert, 400 for a bad id", async () => {
    expect((await updateAuto(req({ enabled: true }, "PUT"), ctx({ id: ID }))).status).toBe(404);
    expect((await updateAuto(req({ enabled: true }, "PUT"), ctx({ id: "x" }))).status).toBe(400);
    expect((await deleteAuto(req(null, "DELETE"), ctx({ id: ID }))).status).toBe(404);
    expect((await previewAuto(req(null), ctx({ id: ID }))).status).toBe(404);
    expect((await testAuto(req(null), ctx({ id: ID }))).status).toBe(404);
    expect((await autoRuns(req(null, "GET"), ctx({ id: "x" }))).status).toBe(400);
  });
  it("preview and send test are queued as test_simulate and test_send, nothing is sent here", async () => {
    await createAuto(req(alert), ctx({}));
    const p = await previewAuto(req(null), ctx({ id: ID }));
    const t = await testAuto(req(null), ctx({ id: ID }));
    expect(await p.json()).toMatchObject({ status: "queued", run_id: 1 });
    expect(await t.json()).toMatchObject({ status: "queued", run_id: 2 });
    expect(state.queued.map((q) => [q.mode, q.automationId, q.connectionId])).toEqual([["test_simulate", ID, null], ["test_send", ID, null]]);
    expect(state.audit.at(-2)![0]).toMatch(/automation\.preview/);
    expect(state.audit.at(-1)![0]).toMatch(/automation\.test/);
  });
  it("deletes and audits", async () => {
    await createAuto(req(alert), ctx({}));
    expect((await deleteAuto(req(null, "DELETE"), ctx({ id: ID }))).status).toBe(200);
    expect(state.audit.at(-1)![0]).toMatch(/automation\.delete/);
  });
  it("runs lists respect and clamp the limit", async () => {
    expect(await (await allRuns(req(null, "GET", "http://x/api/automation-runs?limit=7"), ctx({}))).json()).toEqual([{ id: 1, limit: 7 }]);
    expect(await (await allRuns(req(null, "GET", "http://x/api/automation-runs?limit=99999"), ctx({}))).json()).toEqual([{ id: 1, limit: 500 }]);
    expect(await (await allRuns(req(null, "GET"), ctx({}))).json()).toEqual([{ id: 1, limit: 100 }]);
    expect(await (await autoRuns(req(null, "GET", "http://x/a?limit=abc"), ctx({ id: ID }))).json()).toEqual([{ id: 2, automation_id: ID, limit: 50 }]);
  });
});
