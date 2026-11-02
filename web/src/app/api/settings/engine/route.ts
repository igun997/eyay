import { route } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { getEngineDefaults, setEngineDefaults } from "@/lib/server/queries";
import { query } from "@/lib/server/db";
import { BUILTIN, checkEffective, parseConfig } from "@/lib/engine-config";

export const dynamic = "force-dynamic";

/** Global engine defaults (docs/ENGINE_CONFIG.md). Admin only: they apply to every camera. */
export const GET = route(async () => {
  await requireSession(["admin"]);
  return { defaults: await getEngineDefaults(), builtin: BUILTIN };
});

export const PUT = route(async (req) => {
  const u = await requireSession(["admin"]);
  const parsed = parseConfig(await req.json().catch(() => null));
  if (!parsed.ok) throw new HttpError(400, parsed.errors.join("; "));
  const errs = checkEffective(parsed.config);
  if (errs.length) throw new HttpError(400, errs.join("; "));
  await setEngineDefaults(parsed.config, u.uid);
  await query("INSERT INTO audit_log (user_id, action, target, detail) VALUES ($1,'engine.defaults.update','defaults',$2)", [u.uid, JSON.stringify(parsed.config)]);
  return { ok: true, defaults: parsed.config, appliedWithinSeconds: 5 };
});
