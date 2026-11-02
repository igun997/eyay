import { tFromRequest } from "@/lib/i18n";
import { route } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { getEngineDefaults, setSourceConfig } from "@/lib/server/queries";
import { query } from "@/lib/server/db";
import { checkEffective, mergeConfig, parseConfig } from "@/lib/engine-config";

/** Per-camera overrides. A leaf sent as null (or left out) inherits from the defaults; only real overrides are stored. */
export const PUT = route<{ id: string }>(async (req, { params }) => {
  const u = await requireSession(["admin"]);
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw new HttpError(400, "err.badId");
  const parsed = parseConfig(await req.json().catch(() => null), "camera", tFromRequest(req));
  if (!parsed.ok) throw new HttpError(400, parsed.errors.join("; "));
  // the camera runs with defaults + its overrides, so that is what has to be coherent (e.g. a line needs a measured distance)
  const errs = checkEffective(mergeConfig(await getEngineDefaults(), parsed.config), tFromRequest(req));
  if (errs.length) throw new HttpError(400, errs.join("; "));
  const row = await setSourceConfig(id, parsed.config);
  if (!row) throw new HttpError(404, "err.sourceNotFound");
  await query("INSERT INTO audit_log (user_id, action, target, detail) VALUES ($1,'source.config',$2,$3)", [u.uid, row.code, JSON.stringify(parsed.config)]);
  return { ok: true, config: parsed.config, appliedWithinSeconds: 5 };
});
