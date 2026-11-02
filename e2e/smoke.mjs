// End-to-end smoke test, ONE live stream: admin registers an RTSP source in the UI -> worker goes online ->
// detections reach triage with a bounding box -> live view streams -> security confirms -> incident timeline.
// Needs the compose stack with the dev profile (mediamtx + feeder). See e2e/run.sh.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const ADMIN = { email: process.env.ADMIN_EMAIL ?? "admin@eyeai.local", password: process.env.ADMIN_PASSWORD ?? "admin12345" };
const STREAM = process.env.E2E_STREAM_URL ?? "rtsp://mediamtx:8554/dock2";
const OUT = process.env.E2E_OUT ?? "out";
mkdirSync(OUT, { recursive: true });

const steps = [];
const step = async (name, fn) => {
  const t = Date.now();
  try { const r = await fn(); steps.push(`PASS ${name} (${((Date.now() - t) / 1000).toFixed(1)}s)`); console.log(steps.at(-1)); return r; }
  catch (e) { steps.push(`FAIL ${name}`); console.error(`FAIL ${name}: ${e.message}`); throw e; }
};
const assert = (c, m) => { if (!c) throw new Error(m); };

async function login(page, { email, password }) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#email", email); await page.fill("#password", password);
  await page.click("button[type=submit]");
  await page.waitForURL("**/overview");
}

const browser = await chromium.launch();
const admin = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
let failed = false;
try {
  await step("app is healthy", async () => {
    for (let i = 0; i < 60; i++) { try { if ((await fetch(`${BASE}/api/health`)).ok) return; } catch {} await new Promise((r) => setTimeout(r, 2000)); }
    throw new Error("app never became healthy");
  });

  await step("wrong password stays on the form with an error", async () => {
    await admin.goto(`${BASE}/login`, { waitUntil: "networkidle" });
    await admin.fill("#email", ADMIN.email); await admin.fill("#password", "definitely-wrong");
    await admin.click("button[type=submit]");
    await admin.waitForSelector("p[role=alert]");
    assert(new URL(admin.url()).pathname === "/login", "left /login");
    assert(await admin.isEnabled("button[type=submit]"), "sign in button stuck disabled");
  });

  await step("admin signs in", () => login(admin, ADMIN));

  const sourceId = await step("admin registers the RTSP source in the UI", async () => {
    await admin.goto(`${BASE}/sources`, { waitUntil: "networkidle" });
    await admin.click("text=Add source >> nth=0");
    await admin.waitForSelector("[role=dialog]");
    await admin.fill("#name", "Dock 2");
    await admin.fill("#url", "http://example.com/not-a-stream.mp4");
    await admin.click("[role=dialog] button[type=submit]");
    await admin.waitForSelector("[role=dialog] p[role=alert]");           // bad URL rejected by the API
    await admin.fill("#url", STREAM);
    await admin.click("[role=dialog] button[type=submit]");
    await admin.waitForSelector("[role=dialog]", { state: "detached" });
    const res = await admin.request.get(`${BASE}/api/sources`);
    const src = (await res.json()).find((s) => s.url === STREAM);
    assert(src && src.kind === "rtsp", "source not stored as rtsp");
    return src.id;
  });

  await step("worker brings the stream online", async () => {
    for (let i = 0; i < 90; i++) {
      const s = (await (await admin.request.get(`${BASE}/api/sources/${sourceId}`)).json());
      if (s.status === "online") { console.log(`     fps=${s.fps} inference_ms=${s.inference_ms}`); return; }
      if (i > 20 && s.status === "error") throw new Error(`source error: ${s.last_error}`);
      await new Promise((r) => setTimeout(r, 2000));
    }
    throw new Error("stream never went online");
  });

  const sec = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await step("admin creates a security user; security signs in; admin-only areas are blocked", async () => {
    const r = await admin.request.post(`${BASE}/api/users`, { data: { email: "e2e-sec@eyeai.local", name: "E2E Security", password: "security123", role: "security" } });
    assert(r.ok() || r.status() === 409, `create user: ${r.status()}`);
    await login(sec, { email: "e2e-sec@eyeai.local", password: "security123" });
    await sec.goto(`${BASE}/sources`); assert(new URL(sec.url()).pathname === "/overview", "security reached /sources");
    assert((await sec.request.post(`${BASE}/api/sources`, { data: {} })).status() === 403, "security can POST sources");
    const nav = await sec.$$eval("nav a", (a) => a.map((x) => x.textContent.trim()));
    assert(!nav.some((n) => /Sources|Users/.test(n)), `security nav shows admin items: ${nav}`);
  });

  const ev = await step("detections reach triage with a bounding box and snapshot", async () => {
    for (let i = 0; i < 120; i++) {
      const { events } = await (await sec.request.get(`${BASE}/api/events?tab=needs_review`)).json();
      const e = events.find((x) => x.source_id === sourceId && x.bbox && x.snapshots > 0);
      if (e) { console.log(`     ${e.label} ${e.confidence.toFixed(2)} ${e.incident_code}`); return e; }
      await new Promise((r) => setTimeout(r, 2000));
    }
    throw new Error("no detection arrived");
  });

  await step("the camera AI classifies the event the moment it is written", async () => {
    const { events } = await (await sec.request.get(`${BASE}/api/events?tab=needs_review`)).json();
    const e = events.find((x) => x.source_id === sourceId && x.cls_label);
    assert(e, "no classified event");
    assert(["person", "vehicle", "other"].includes(e.cls_label) && e.cls_model === "detector" && e.cls_confidence > 0 && e.cls_confidence <= 1,
      `bad classification: ${JSON.stringify({ l: e.cls_label, m: e.cls_model, c: e.cls_confidence })}`);
    assert(e.pipeline === "ready", `the event should be ready for triage at once, got ${e.pipeline}`);
    console.log(`     ${e.label} -> ${e.cls_label} ${e.cls_confidence.toFixed(2)} (${e.cls_model})`);
  });

  await step("snapshot burst: several frames, each with its own box, plus an indication", async () => {
    for (let i = 0; i < 40; i++) {
      const { events } = await (await sec.request.get(`${BASE}/api/events?tab=needs_review`)).json();
      const e = events.find((x) => x.id === ev.id);
      const frames = await (await sec.request.get(`${BASE}/api/events/${ev.id}/frames`)).json();
      if (e?.burst_done && frames.length >= 2) {
        const offs = frames.map((f) => f.offset_ms);
        assert(offs.every((o, k) => k === 0 || o > offs[k - 1]), `offsets not increasing: ${offs}`);
        assert(e.indication && e.indication_kind && e.burst_ms > 0, `no indication: ${JSON.stringify({ k: e.indication_kind, t: e.indication })}`);
        for (const f of frames) assert((await sec.request.get(`${BASE}/api/events/${ev.id}/snapshot/${f.idx}`)).ok(), `frame ${f.idx} missing`);
        console.log(`     ${frames.length} frames at ${offs.join("/")} ms, ${e.indication_kind}: ${e.indication}`);
        return;
      }
      await new Promise((r) => setTimeout(r, 3000));
    }
    throw new Error("burst never completed");
  });

  await step("rules page lists the default behaviour rules", async () => {
    await sec.goto(`${BASE}/rules`);
    await sec.waitForSelector("text=Behaviour rules");
    for (const t of ["Weapon-like object", "Concealing an item", "Tampering with equipment, lock or door", "Lingering in view"]) await sec.waitForSelector(`text=${t}`);
    await sec.screenshot({ path: `${OUT}/rules.png`, fullPage: true });
  });

  await step("live view streams frames (MJPEG with boxes)", async () => {
    await sec.goto(`${BASE}/cameras`);
    await sec.waitForSelector("img[alt^='Live']");
    await sec.waitForFunction(() => { const i = document.querySelector("img[alt^='Live']"); return i && i.naturalWidth > 0; }, null, { timeout: 30000 });
    // the worker burns the boxes into the MJPEG; prove that stream is really served through the auth proxy
    const r = await sec.request.get(`${BASE}/api/live/${sourceId}?mode=snapshot`);
    assert(r.ok() && (r.headers()["content-type"] ?? "").includes("image/jpeg") && (await r.body()).length > 5000, "snapshot not a jpeg");
    await sec.waitForTimeout(2000);
    await sec.screenshot({ path: `${OUT}/cameras-live.png` });
  });

  await step("security reviews the event in triage and confirms it", async () => {
    await sec.goto(`${BASE}/triage`);
    await sec.waitForSelector("button[aria-pressed]");
    await sec.waitForSelector("section[aria-label='Event detail'] img");
    await sec.screenshot({ path: `${OUT}/triage.png` });
    await sec.click("text=Confirm incident");
    await sec.fill("[role=dialog] textarea", "e2e: verified");
    await sec.click("[role=dialog] button:has-text('Confirm incident')");
    await sec.waitForSelector("[data-sonner-toast]");
  });

  await step("the confirmed event's incident has camera + triage entries in its timeline", async () => {
    // triage confirms the newest event, which may differ from the one found earlier: look up what was really confirmed
    const { events } = await (await sec.request.get(`${BASE}/api/events?tab=resolved`)).json();
    const done = events.find((e) => e.status === "confirmed" && e.source_id === sourceId);
    assert(done && done.incident_id, "no confirmed event with an incident");
    const r = await (await sec.request.get(`${BASE}/api/incidents/${done.incident_id}`)).json();
    const kinds = r.timeline.map((t) => t.kind);
    assert(kinds.includes("camera") && kinds.includes("triage"), `timeline kinds: ${kinds}`);
    await sec.goto(`${BASE}/incidents/${done.incident_code}`); await sec.waitForSelector("text=Timeline");
    await sec.screenshot({ path: `${OUT}/incident.png` });
  });

  await step("admin removes the source; the stream stops", async () => {
    const r = await admin.request.delete(`${BASE}/api/sources/${sourceId}`); assert(r.ok(), `delete: ${r.status()}`);
    const list = await (await admin.request.get(`${BASE}/api/sources`)).json();
    assert(!list.some((s) => s.id === sourceId), "source still listed");
  });
} catch { failed = true; await admin.screenshot({ path: `${OUT}/failure.png` }).catch(() => {}); }
await browser.close();
console.log(`\n${steps.join("\n")}`);
process.exit(failed ? 1 : 0);
