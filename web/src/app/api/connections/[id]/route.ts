import { tFromRequest } from "@/lib/i18n";
import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { automationsUsingConnection, deleteConnection, getConnection, updateConnection } from "@/lib/server/automations";
import { query } from "@/lib/server/db";
import { connectionPatch, maskConfig, mergeSecrets, parseConnectionConfig } from "@/lib/automations";

const isId = (id: string) => /^[0-9a-f-]{36}$/i.test(id);

export const PUT = route<{ id: string }>(async (req, { params }) => {
  const u = await requireSession(["admin"]);
  const { id } = await params;
  if (!isId(id)) throw new HttpError(400, "err.badId");
  const v = await body(req, connectionPatch);
  const cur = await getConnection(id);
  if (!cur) throw new HttpError(404, "err.connectionNotFound");
  let config: Record<string, unknown> | undefined;
  if (v.config !== undefined) {
    const parsed = parseConnectionConfig(cur.kind, mergeSecrets(cur.kind, cur.config, v.config), tFromRequest(req));
    if (!parsed.ok) throw new HttpError(400, parsed.errors.join("; "));
    config = parsed.config;
  }
  try {
    const row = await updateConnection(id, { name: v.name, config, enabled: v.enabled });
    await query("INSERT INTO audit_log (user_id, action, target, detail) VALUES ($1,'connection.update',$2,$3)", [u.uid, row!.name, JSON.stringify({ fields: Object.keys(v) })]);
    return { ...row!, config: maskConfig(row!.kind, row!.config) };
  } catch (e) {
    if ((e as { code?: string }).code === "23505") throw new HttpError(409, "err.connectionExists", { name: v.name ?? "" });
    throw e;
  }
});

export const DELETE = route<{ id: string }>(async (_req, { params }) => {
  const u = await requireSession(["admin"]);
  const { id } = await params;
  if (!isId(id)) throw new HttpError(400, "err.badId");
  const users = await automationsUsingConnection(id);
  if (users.length) throw new HttpError(409, "err.connectionInUse", { names: users.map((a) => `“${a.name}”`).join(", ") });
  const row = await deleteConnection(id);
  if (!row) throw new HttpError(404, "err.connectionNotFound");
  await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'connection.delete',$2)", [u.uid, row.name]);
  return { ok: true };
});
