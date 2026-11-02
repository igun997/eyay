import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { deleteAutomation, getAutomation, listConnections, updateAutomation } from "@/lib/server/automations";
import { query } from "@/lib/server/db";
import { automationPatch, checkActionConnections } from "@/lib/automations";

const isId = (id: string) => /^[0-9a-f-]{36}$/i.test(id);

/** Partial update. Switching to live needs `"confirm_live": true` and is recorded under the admin's name. */
export const PUT = route<{ id: string }>(async (req, { params }) => {
  const u = await requireSession(["admin"]);
  const { id } = await params;
  if (!isId(id)) throw new HttpError(400, "Bad id");
  const { confirm_live, ...v } = await body(req, automationPatch);
  const cur = await getAutomation(id);
  if (!cur) throw new HttpError(404, "Alert not found");
  if (v.actions) {
    const errs = checkActionConnections(v.actions, await listConnections());
    if (errs.length) throw new HttpError(400, errs.join("; "));
  }
  const row = await updateAutomation(id, v);
  const wentLive = v.mode === "live" && cur.mode !== "live";
  await query("INSERT INTO audit_log (user_id, action, target, detail) VALUES ($1,$2,$3,$4)", [u.uid, wentLive ? "automation.live" : "automation.update", cur.name, JSON.stringify({ fields: Object.keys(v), confirm_live: !!confirm_live })]);
  return row;
});

export const DELETE = route<{ id: string }>(async (_req, { params }) => {
  const u = await requireSession(["admin"]);
  const { id } = await params;
  if (!isId(id)) throw new HttpError(400, "Bad id");
  const row = await deleteAutomation(id);
  if (!row) throw new HttpError(404, "Alert not found");
  await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'automation.delete',$2)", [u.uid, row.name]);
  return { ok: true };
});
