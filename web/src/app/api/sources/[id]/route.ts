import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { getSource, maskCreds } from "@/lib/server/queries";
import { query } from "@/lib/server/db";
import { sourcePatch, kindOf } from "@/lib/schemas";

export const GET = route<{ id: string }>(async (_r, { params }) => {
  const u = await requireSession();
  const s = await getSource((await params).id);
  if (!s) throw new HttpError(404, "Source not found");
  return u.role === "admin" ? s : maskCreds(s);
});

export const PATCH = route<{ id: string }>(async (req, { params }) => {
  const u = await requireSession(["admin"]);
  const { id } = await params;
  const v = await body(req, sourcePatch);
  const sets: string[] = [];
  const p: unknown[] = [];
  const set = (col: string, val: unknown) => { p.push(val); sets.push(`${col} = $${p.length}`); };
  if (v.url !== undefined) {
    const kind = kindOf(v.url);
    if (!kind) throw new HttpError(400, "Use an rtsp:// URL or an http(s) URL ending in .m3u8");
    set("url", v.url); set("kind", kind);
  }
  for (const k of ["name", "labels", "min_confidence", "enabled"] as const) if (v[k] !== undefined) set(k, v[k]);
  if (!sets.length) throw new HttpError(400, "Nothing to update");
  // a changed url/enabled flag makes the worker reconnect: reset runtime state
  if (v.url !== undefined || v.enabled !== undefined) { set("status", v.enabled === false ? "offline" : "pending"); sets.push("last_error = NULL"); }
  p.push(id);
  const rows = await query(`UPDATE sources SET ${sets.join(", ")}, updated_at = now() WHERE id = $${p.length} RETURNING code`, p);
  if (!rows.length) throw new HttpError(404, "Source not found");
  await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'source.update',$2)", [u.uid, rows[0].code]);
  return { ok: true };
});

export const DELETE = route<{ id: string }>(async (_r, { params }) => {
  const u = await requireSession(["admin"]);
  const rows = await query("DELETE FROM sources WHERE id = $1 RETURNING code", [(await params).id]);
  if (!rows.length) throw new HttpError(404, "Source not found");
  await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'source.delete',$2)", [u.uid, rows[0].code]);
  return { ok: true };
});
