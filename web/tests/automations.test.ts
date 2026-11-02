import { describe, expect, it } from "vitest";
import {
  SECRET_MASK, actionSchema, automationInput, automationPatch, checkActionConnections, conditionSchema, connectionInput, describeAutomation, maskConfig,
  mergeSecrets, parseConnectionConfig, waitForRun, type Action, type Condition, type RunRow,
} from "@/lib/automations";
import { OBJECT_CHOICES, SENSITIVITY_VALUES, sensitivityKey, sensitivityOf } from "@/lib/plain";
import { agreement, clsLabelKey, isSecondCheck } from "@/lib/classification";
import { makeT } from "@/lib/i18n";

const en = makeT("en");
const id = makeT("id");
import { normaliseRule, ruleInput, ruleUpdate } from "@/lib/schemas";

const ID = "9ea0bb5d-faa9-4b16-a51d-d9579ba64b39";
const ID2 = "3bc611c7-0cdd-4f3a-9a2e-0b6f5f7f1b10";

describe("conditions", () => {
  it.each([
    [{ field: "camera", in: [ID] }], [{ field: "label", in: ["person", "car"] }], [{ field: "severity", at_least: "HIGH" }], [{ field: "indicator", triggered: "*" }],
    [{ field: "indicator", triggered: "weapon" }], [{ field: "classified_as", is: "false_alarm" }], [{ field: "classified_as", is: "other" }], [{ field: "speed_kmh", above: 60 }],
    [{ field: "suspicious", is: true }], [{ field: "time", between: ["22:00", "06:00"], utc_offset_min: 420 }],
  ])("accepts %j", (c) => expect(conditionSchema.safeParse(c).success).toBe(true));
  it.each([
    [{ field: "camera", in: [] }], [{ field: "camera", in: ["not-an-id"] }], [{ field: "label", in: [] }], [{ field: "severity", at_least: "URGENT" }],
    [{ field: "indicator", triggered: "Bad Key" }], [{ field: "classified_as", is: "dog" }], [{ field: "speed_kmh", above: -1 }], [{ field: "suspicious", is: "yes" }],
    [{ field: "time", between: ["25:00", "06:00"], utc_offset_min: 0 }], [{ field: "time", between: ["22:00", "06:00"], utc_offset_min: 900 }],
    [{ field: "time", between: ["22:00"], utc_offset_min: 0 }], [{ field: "mood", is: 1 }], [{ field: "severity", at_least: "HIGH", extra: 1 }],
  ])("rejects %j", (c) => expect(conditionSchema.safeParse(c).success).toBe(false));
});

describe("actions", () => {
  it("accepts the four shapes of the contract", () => {
    for (const a of [
      { type: "webhook", connection: ID }, { type: "webhook", connection: ID, body: "{{event.label}}" },
      { type: "mqtt", connection: ID, topic: "plant/alarm", payload: "x", retain: true },
      { type: "modbus", connection: ID, function: "write_coil", address: 0, hold_s: 5 },
      { type: "modbus", connection: ID, function: "write_register", address: 10, value: 1 },
    ]) expect(actionSchema.safeParse(a).success).toBe(true);
  });
  it.each([
    [{ type: "modbus", connection: ID, function: "write_coil", address: 0, hold_s: 0 }], [{ type: "modbus", connection: ID, function: "write_coil", address: 0, hold_s: 61 }],
    [{ type: "modbus", connection: ID, function: "write_coil", address: 65536, hold_s: 5 }], [{ type: "modbus", connection: ID, function: "write_register", address: 0, value: 65536 }],
    [{ type: "modbus", connection: ID, function: "write_register", address: -1, value: 1 }], [{ type: "modbus", connection: ID, function: "write_coil", address: 0 }],
    [{ type: "mqtt", connection: ID, topic: "plant/#" }], [{ type: "mqtt", connection: ID, topic: "" }], [{ type: "mqtt", connection: ID, topic: "a b" }],
    [{ type: "email", connection: ID }], [{ type: "webhook", connection: "x" }], [{ type: "webhook", connection: ID, body: "x".repeat(4001) }],
  ])("rejects %j", (a) => expect(actionSchema.safeParse(a).success).toBe(false));
  it("a coil only pulses: there is no latching option", () => {
    expect(actionSchema.safeParse({ type: "modbus", connection: ID, function: "write_coil", address: 1, hold_s: 5, latch: true }).success).toBe(false);
  });
  it("each action must name an enabled connection of the same kind", () => {
    const cs = [{ id: ID, kind: "webhook", enabled: true, name: "Hook" }, { id: ID2, kind: "modbus", enabled: false, name: "PLC" }];
    expect(checkActionConnections([{ type: "webhook", connection: ID }], cs, en)).toEqual([]);
    expect(checkActionConnections([{ type: "mqtt", connection: ID, topic: "t", retain: false }], cs, en)[0]).toMatch(/webhook connection/);
    expect(checkActionConnections([{ type: "modbus", connection: ID2, function: "write_coil", address: 0, hold_s: 1 }], cs, en)[0]).toMatch(/switched off/);
    expect(checkActionConnections([{ type: "webhook", connection: "00000000-0000-0000-0000-000000000000" }], cs, en)[0]).toMatch(/does not exist/);
  });
});

describe("automation input", () => {
  const base = { name: "Night person", actions: [{ type: "webhook", connection: ID }] };
  it("starts in practice mode, switched off, with the documented limits", () => {
    expect(automationInput.parse(base)).toMatchObject({ mode: "simulate", enabled: false, conditions: [], cooldown_s: 60, max_per_hour: 30, description: "" });
  });
  it("going live needs confirm_live: true", () => {
    expect(automationInput.safeParse({ ...base, mode: "live" }).success).toBe(false);
    expect(automationInput.safeParse({ ...base, mode: "live", confirm_live: false }).success).toBe(false);
    expect(automationInput.safeParse({ ...base, mode: "live", confirm_live: true }).success).toBe(true);
    expect(automationPatch.safeParse({ mode: "live" }).success).toBe(false);
    expect(automationPatch.safeParse({ mode: "live", confirm_live: true }).success).toBe(true);
    expect(automationPatch.safeParse({ mode: "simulate" }).success).toBe(true);
  });
  it("checks names, limits and at least one action", () => {
    for (const bad of [{ name: "" }, { name: "x".repeat(81) }, { actions: [] }, { cooldown_s: -1 }, { cooldown_s: 86401 }, { max_per_hour: 0 }, { max_per_hour: 1001 }, { extra: 1 }])
      expect(automationInput.safeParse({ ...base, ...bad }).success).toBe(false);
    expect(automationInput.safeParse({ ...base, cooldown_s: 0, max_per_hour: 1000 }).success).toBe(true);
  });
  it("a patch changes only what it sends: no default leaks into a partial update", () => {
    expect(automationPatch.parse({ enabled: true })).toEqual({ enabled: true });
    expect(automationPatch.parse({ mode: "simulate" })).toEqual({ mode: "simulate" });
    expect(automationPatch.safeParse({}).success).toBe(false);
  });
});

describe("connections", () => {
  it("validates each kind's config and fills the defaults", () => {
    expect(parseConnectionConfig("webhook", { url: "https://h.example/x" })).toEqual({ ok: true, config: { url: "https://h.example/x", method: "POST", headers: {}, timeout_s: 5 } });
    expect(parseConnectionConfig("mqtt", { host: "broker" })).toMatchObject({ ok: true, config: { host: "broker", port: 1883, client_id: "eyeai", username: "" } });
    expect(parseConnectionConfig("modbus", { host: "10.0.0.5" })).toMatchObject({ ok: true, config: { port: 502, unit_id: 1 } });
  });
  it.each([
    ["webhook", { url: "ftp://x" }], ["webhook", { url: "https://x", method: "GET" }], ["webhook", { url: "https://x", headers: { "bad header": "1" } }], ["webhook", { url: "https://x", extra: 1 }],
    ["mqtt", { host: "" }], ["mqtt", { host: "b", port: 70000 }], ["modbus", { host: "plc", unit_id: 300 }], ["modbus", { host: "p", timeout_s: 31 }], ["modbus", { host: "has space" }],
  ] as const)("rejects %s %j", (kind, cfg) => expect(parseConnectionConfig(kind, cfg).ok).toBe(false));
  it("names are 1..80 characters and unknown keys are rejected", () => {
    expect(connectionInput.safeParse({ name: "", kind: "webhook" }).success).toBe(false);
    expect(connectionInput.safeParse({ name: "x".repeat(81), kind: "webhook" }).success).toBe(false);
    expect(connectionInput.safeParse({ name: "a", kind: "email" }).success).toBe(false);
    expect(connectionInput.safeParse({ name: "a", kind: "webhook", nope: 1 }).success).toBe(false);
  });
  it("secrets are returned as ******** and never in clear text", () => {
    const wh = maskConfig("webhook", { url: "https://x", secret: "s3cret", headers: { "X-Api-Key": "abc", Empty: "" } });
    expect(wh).toMatchObject({ secret: SECRET_MASK, headers: { "X-Api-Key": SECRET_MASK, Empty: "" }, url: "https://x" });
    expect(JSON.stringify(wh)).not.toMatch(/s3cret|abc/);
    expect(maskConfig("mqtt", { host: "b", password: "pw" }).password).toBe(SECRET_MASK);
    expect(maskConfig("mqtt", { host: "b" })).not.toHaveProperty("password");
    expect(maskConfig("webhook", { url: "https://x", secret: "" }).secret).toBe("");
  });
  it("sending ******** or omitting a secret keeps it, an empty string clears it, anything else replaces it", () => {
    const stored = { url: "https://x", secret: "old", headers: { "X-Api-Key": "abc" } };
    expect(mergeSecrets("webhook", stored, { url: "https://y", secret: SECRET_MASK, headers: { "X-Api-Key": SECRET_MASK } })).toMatchObject({ secret: "old", headers: { "X-Api-Key": "abc" }, url: "https://y" });
    expect(mergeSecrets("webhook", stored, { url: "https://x" }).secret).toBe("old");
    expect(mergeSecrets("webhook", stored, { url: "https://x", secret: "" })).not.toHaveProperty("secret");
    expect(mergeSecrets("webhook", stored, { url: "https://x", secret: "new", headers: { "X-Api-Key": "k2" } })).toMatchObject({ secret: "new", headers: { "X-Api-Key": "k2" } });
    expect(mergeSecrets("mqtt", { host: "b", password: "pw" }, { host: "b", password: SECRET_MASK }).password).toBe("pw");
    expect(mergeSecrets("mqtt", {}, { host: "b", password: SECRET_MASK })).not.toHaveProperty("password");
  });
});

describe("plain-language description", () => {
  const cams = [{ id: ID, name: "Dock 2" }];
  const conns = [{ id: ID2, name: "Control room" }];
  it("reads like a sentence", () => {
    const conds: Condition[] = [{ field: "camera", in: [ID] }, { field: "label", in: ["person"] }, { field: "severity", at_least: "HIGH" }];
    const acts: Action[] = [{ type: "webhook", connection: ID2 }, { type: "modbus", connection: ID2, function: "write_coil", address: 3, hold_s: 5 }];
    expect(describeAutomation(conds, acts, cams, conns, [], en)).toBe("When it is on Dock 2 and it sees a person and it is at least high priority, then send a message to Control room, then switch output 3 on for 5 s on Control room.");
  });
  it("says so when there are no conditions", () => {
    expect(describeAutomation([], [{ type: "webhook", connection: ID2 }], cams, conns, [], en)).toBe("When any event happens, then send a message to Control room.");
  });
  it("names warning signs and the AI check in plain words", () => {
    expect(describeAutomation([{ field: "indicator", triggered: "weapon" }, { field: "classified_as", is: "false_alarm" }, { field: "speed_kmh", above: 60 }, { field: "time", between: ["22:00", "06:00"], utc_offset_min: 420 }], [], [], [], [{ key: "weapon", title: "Weapon-like object" }], en))
      .toBe("When the warning sign “Weapon-like object” appeared and the AI check says it is not a real detection and it moves faster than 60 km/h and the time is between 22:00 and 06:00, then do nothing yet.");
  });
});

describe("waiting for a test run", () => {
  const instant = async () => {};
  const run = (status: RunRow["status"]): RunRow => ({ id: 1, status });
  it("returns the run as soon as it leaves queued", async () => {
    const seq: (RunRow | undefined)[] = [run("queued"), run("queued"), run("simulated")];
    let calls = 0;
    expect((await waitForRun(async () => seq[calls++], {}, instant))?.status).toBe("simulated");
    expect(calls).toBe(3);
  });
  it("polls every second for 15 seconds, then gives up with null", async () => {
    let calls = 0; const slept: number[] = [];
    expect(await waitForRun(async () => { calls++; return run("queued"); }, {}, async (ms) => { slept.push(ms); })).toBeNull();
    expect(slept.every((ms) => ms === 1000)).toBe(true);
    expect(slept.length).toBe(15);
    expect(calls).toBe(16);
  });
  it("a row that does not exist yet counts as waiting", async () => {
    let calls = 0;
    expect((await waitForRun(async () => (++calls < 3 ? undefined : run("failed")), {}, instant))?.status).toBe("failed");
  });
});

describe("sensitivity (plain words for the threshold)", () => {
  it("high sensitivity means a LOW threshold: more warning signs", () => {
    expect(sensitivityOf(0.9)).toBe("low"); expect(sensitivityOf(0.7)).toBe("medium"); expect(sensitivityOf(0.35)).toBe("high");
    expect(en(sensitivityKey(0.75))).toBe("Medium");
    expect(id(sensitivityKey(0.75))).toBe("Sedang");
  });
  it("the presets land in their own bucket", () => {
    for (const k of ["low", "medium", "high"] as const) expect(sensitivityOf(SENSITIVITY_VALUES[k])).toBe(k);
  });
});

describe("rule kind 'spotted next to a person'", () => {
  const base = { title: "Knife near a person", description: "The camera AI sees a knife next to a person.", kind: "object", applies_to: ["person"], severity: "HIGH", threshold: 0.35, config: { objects: ["knife", "scissors"], margin: 0.3 } };
  it("accepts objects from the list and a margin in 0..1", () => {
    expect(ruleInput.safeParse({ ...base, key: "knife_near" }).success).toBe(true);
    for (const margin of [0, 1]) expect(ruleUpdate.safeParse({ ...base, config: { objects: ["knife"], margin } }).success).toBe(true);
    expect(OBJECT_CHOICES).toContain("cell phone");
  });
  it.each([{ objects: [] }, { objects: ["laser"] }, { objects: ["knife"], margin: 1.1 }, { objects: ["knife"], margin: -0.1 }, { margin: 0.3 }])("rejects config %j", (config) =>
    expect(ruleUpdate.safeParse({ ...base, config }).success).toBe(false));
  it("needs no question; the stored config keeps only objects and margin (margin defaults to 0.3)", () => {
    const v = normaliseRule(ruleUpdate.parse({ ...base, question: "ignored?", config: { objects: ["knife"] } }));
    expect(v.question).toBeNull();
    expect(v.config).toEqual({ objects: ["knife"], margin: 0.3 });
    expect(ruleUpdate.safeParse({ ...base, threshold: 0 }).success).toBe(false);
  });
});

describe("classification display", () => {
  it("renders 'other' as Other object and tells the camera AI from the second check", () => {
    expect(en(clsLabelKey("other"))).toBe("Other object");
    expect(id(clsLabelKey("other"))).toBe("Objek lain");
    expect(en(clsLabelKey("false_alarm"))).toBe("Not a real detection");
    expect(isSecondCheck("detector")).toBe(false);
    expect(isSecondCheck(null)).toBe(false);
    expect(isSecondCheck("d1")).toBe(true);
  });
  it("'other' never counts as a disagreement", () => {
    expect(agreement("dog", "other")).toBe("unknown");
    expect(agreement("person", "other")).toBe("unknown");
  });
});

describe("the sentence in Indonesian (the default language)", () => {
  it("reads naturally", () => {
    const conds: Condition[] = [{ field: "label", in: ["person"] }, { field: "severity", at_least: "HIGH" }];
    expect(describeAutomation(conds, [{ type: "webhook", connection: ID }], [], [{ id: ID, name: "Ruang kendali" }], [], id))
      .toBe("Jika kamera melihat orang dan tingkatnya minimal tinggi, maka kirim pesan ke Ruang kendali.");
    expect(describeAutomation([], [], [], [], [], id)).toBe("Jika ada kejadian apa pun, maka belum ada tindakan.");
  });
});
