import { describe, expect, it } from "vitest";
import { demoCameras, runSeeds, truthy } from "../scripts/seeds";

function fakeDb(seeded = false) {
  const calls: { sql: string; params?: unknown[] }[] = [];
  return {
    calls,
    query: async (sql: string, params?: unknown[]) => {
      calls.push({ sql, params });
      if (sql.startsWith("SELECT 1 FROM seed_runs")) return { rowCount: seeded ? 1 : 0, rows: [] };
      return { rowCount: 0, rows: [] };
    },
  } as never as Parameters<typeof runSeeds>[0] & { calls: typeof calls };
}

const inserts = (db: { calls: { sql: string; params?: unknown[] }[] }) => db.calls.filter((c) => c.sql.startsWith("INSERT INTO sources"));

describe("demo seed", () => {
  it("knows the three demo cameras, plus two UCF cameras on request, and trims the base address", () => {
    expect(demoCameras("rtsp://mediamtx:8554/", false).map((c) => c.code)).toEqual(["DEMO-01", "DEMO-02", "DEMO-03"]);
    expect(demoCameras("rtsp://x", true).map((c) => c.code)).toEqual(["DEMO-01", "DEMO-02", "DEMO-03", "UCF-BURG", "UCF-ASLT"]);
    expect(demoCameras("rtsp://mediamtx:8554/", false)[0].url).toBe("rtsp://mediamtx:8554/dock2");
    expect(demoCameras("rtsp://x", false)[2].config).toMatchObject({ calibration: { method: "line", line_axis: "y" } });
  });

  it("does nothing unless asked for", async () => {
    const db = fakeDb();
    expect(await runSeeds(db, {}, () => {})).toEqual([]);
    expect(await runSeeds(db, { SEED_DEMO: "0" }, () => {})).toEqual([]);
    expect(db.calls).toHaveLength(0);
    expect(["1", "true", "YES", "on"].every(truthy) && !truthy("0") && !truthy(undefined)).toBe(true);
  });

  it("registers the cameras once, in one transaction, and records the run", async () => {
    const db = fakeDb(false);
    const ran = await runSeeds(db, { SEED_DEMO: "1", SEED_STREAM_BASE: "rtsp://cams:8554" }, () => {});
    expect(ran).toEqual(["demo-cameras"]);
    expect(inserts(db)).toHaveLength(3);
    expect(inserts(db)[0].params?.[2]).toBe("rtsp://cams:8554/dock2");
    expect(inserts(db)[0].sql).toContain("ON CONFLICT (code) DO NOTHING");
    const sqls = db.calls.map((c) => c.sql);
    expect(sqls.indexOf("BEGIN")).toBeLessThan(sqls.findIndex((s) => s.startsWith("INSERT INTO seed_runs")));
    expect(sqls.at(-1)).toBe("COMMIT");
  });

  it("never runs a second time, even with the variable still set", async () => {
    const db = fakeDb(true);
    expect(await runSeeds(db, { SEED_DEMO: "1" }, () => {})).toEqual([]);
    expect(inserts(db)).toHaveLength(0);
  });

  it("rolls back and lets the app start when a seed fails", async () => {
    const calls: string[] = [];
    const db = {
      query: async (sql: string) => {
        calls.push(sql);
        if (sql.startsWith("INSERT INTO sources")) throw new Error("boom");
        return { rowCount: 0, rows: [] };
      },
    } as never as Parameters<typeof runSeeds>[0];
    const logs: string[] = [];
    expect(await runSeeds(db, { SEED_DEMO: "1" }, (m) => logs.push(m))).toEqual([]);
    expect(calls).toContain("ROLLBACK");
    expect(logs.at(-1)).toContain("FAILED");
  });
});
