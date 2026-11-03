// One-time seeds that run together with the migrations (scripts/migrate.ts), when asked for with an environment variable.
//   SEED_DEMO=1            register the demo cameras once (the feeder of the `dev` compose profile plays their videos)
//   SEED_DEMO_UCF=1        also register the two optional UCF-Crime evaluation cameras (needs scripts/fetch_ucf_clips.sh)
//   SEED_STREAM_BASE=...   where the streams are served (default rtsp://mediamtx:8554)
// "Once" means once per database: the run is recorded in `seed_runs`, so restarting with the variable still set does nothing, and
// cameras an admin later deletes or edits are never brought back. Cameras whose code already exists are left untouched.
import type { PoolClient } from "pg";

export type DemoCamera = { code: string; name: string; url: string; labels: string[]; minConfidence: number; config: Record<string, unknown> };

export const truthy = (v?: string) => ["1", "true", "yes", "on"].includes((v ?? "").trim().toLowerCase());

export function demoCameras(base: string, ucf: boolean): DemoCamera[] {
  const b = base.replace(/\/+$/, "");
  const all = ["person", "car", "truck", "bus", "motorcycle", "bicycle"];
  const cams: DemoCamera[] = [
    { code: "DEMO-01", name: "Dermaga (demo)", url: `${b}/dock2`, labels: all, minConfidence: 0.4, config: {} },
    { code: "DEMO-02", name: "Lorong toko (demo)", url: `${b}/aisle`, labels: ["person"], minConfidence: 0.4, config: {} },
    {
      code: "DEMO-03", name: "Gerbang (demo)", url: `${b}/gate`, labels: ["car", "truck", "bus"], minConfidence: 0.4,
      // example speed setup: the distance is a placeholder, not a field measurement
      config: { calibration: { method: "line", line_axis: "y", line1: 0.3, line2: 0.7, distance_m: 15, speed_limit_kmh: 30 }, tracker: { max_age: 15 } },
    },
  ];
  if (ucf) {
    const l = ["person", "car", "truck", "motorcycle", "bicycle"];
    cams.push(
      { code: "UCF-BURG", name: "Uji: pembobolan (UCF-Crime)", url: `${b}/burglary`, labels: l, minConfidence: 0.35, config: {} },
      { code: "UCF-ASLT", name: "Uji: penyerangan (UCF-Crime)", url: `${b}/assault`, labels: l, minConfidence: 0.35, config: {} },
    );
  }
  return cams;
}

type Db = Pick<PoolClient, "query">;

/** Runs the requested seeds that have not run before. Never throws: a failed seed is logged and rolled back, the app still starts. */
export async function runSeeds(c: Db, env: Record<string, string | undefined>, log: (m: string) => void = console.log): Promise<string[]> {
  const ran: string[] = [];
  if (!truthy(env.SEED_DEMO)) return ran;
  try {
    await c.query("CREATE TABLE IF NOT EXISTS seed_runs (name text PRIMARY KEY, at timestamptz NOT NULL DEFAULT now())");
    if ((await c.query("SELECT 1 FROM seed_runs WHERE name = 'demo-cameras'")).rowCount) {
      log("demo seed: already ran once, skipping");
      return ran;
    }
    await c.query("BEGIN");
    try {
      const cams = demoCameras(env.SEED_STREAM_BASE ?? "rtsp://mediamtx:8554", truthy(env.SEED_DEMO_UCF));
      for (const k of cams) {
        await c.query(
          "INSERT INTO sources (code, name, url, kind, labels, min_confidence, config) VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (code) DO NOTHING",
          [k.code, k.name, k.url, k.url.startsWith("rtsp") ? "rtsp" : "m3u8", k.labels, k.minConfidence, JSON.stringify(k.config)],
        );
      }
      await c.query("INSERT INTO seed_runs (name) VALUES ('demo-cameras')");
      await c.query("COMMIT");
      ran.push("demo-cameras");
      log(`demo seed: registered ${cams.length} cameras (${cams.map((k) => k.code).join(", ")})`);
    } catch (e) {
      await c.query("ROLLBACK");
      throw e;
    }
  } catch (e) {
    log(`demo seed FAILED (the app starts anyway): ${(e as Error).message}`);
  }
  return ran;
}
