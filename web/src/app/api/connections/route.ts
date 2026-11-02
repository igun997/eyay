import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { insertConnection, listConnections } from "@/lib/server/automations";
import { query } from "@/lib/server/db";
import { connectionInput, maskConfig, parseConnectionConfig } from "@/lib/automations";

export const dynamic = "force-dynamic";

/** Where alerts go. Secrets come back as ******** and are never shown. */
export const GET = route(async () => {
  await requireSession();
  return (await listConnections()).map((c) => ({ ...c, config: maskConfig(c.kind, c.config) }));
});

export const POST = route(async (req) => {
  const u = await requireSession(["admin"]);
  const v = await body(req, connectionInput);
  const cfg = parseConnectionConfig(v.kind, v.config);
  if (!cfg.ok) throw new HttpError(400, cfg.errors.join("; "));
  try {
    const row = await insertConnection(v.name, v.kind, cfg.config, v.enabled, u.uid);
    await query("INSERT INTO audit_log (user_id, action, target, detail) VALUES ($1,'connection.create',$2,$3)", [u.uid, v.name, JSON.stringify({ kind: v.kind })]);
    return { ...row!, config: maskConfig(v.kind, cfg.config) };
  } catch (e) {
    if ((e as { code?: string }).code === "23505") throw new HttpError(409, `A connection named “${v.name}” already exists`);
    throw e;
  }
});
