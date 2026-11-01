import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { tx } from "@/lib/server/db";
import { incidentAction } from "@/lib/schemas";

export const POST = route<{ id: string }>(async (req, { params }) => {
  const u = await requireSession();
  const { id } = await params;
  const v = await body(req, incidentAction);
  return tx(async (q) => {
    const [inc] = await q<{ id: string; status: string }>("SELECT id, status FROM incidents WHERE id=$1 FOR UPDATE", [id]);
    if (!inc) throw new HttpError(404, "Incident not found");
    const log = (title: string, detail?: string) =>
      q("INSERT INTO incident_timeline (incident_id, kind, title, detail, user_id) VALUES ($1,'operator',$2,$3,$4)", [id, title, detail ?? null, u.uid]);
    if (v.action === "acknowledge") {
      await q("UPDATE incidents SET status='acknowledged' WHERE id=$1 AND status='open'", [id]);
      await log("Acknowledged");
    } else if (v.action === "assign") {
      let who = "Unassigned";
      if (v.userId) {
        const [t] = await q<{ name: string }>("SELECT name FROM users WHERE id=$1 AND active", [v.userId]);
        if (!t) throw new HttpError(400, "User not found");
        who = t.name;
      }
      await q("UPDATE incidents SET assigned_to=$2 WHERE id=$1", [id, v.userId]);
      await log(v.userId ? `Assigned to ${who}` : "Unassigned", `By ${u.name}`);
    } else if (v.action === "resolve") {
      await q("UPDATE incidents SET status='resolved', resolved_at=now(), resolved_by=$2 WHERE id=$1", [id, u.uid]);
      await log("Resolved", `By ${u.name}`);
    } else {
      await q("UPDATE incidents SET status='open', resolved_at=NULL, resolved_by=NULL WHERE id=$1", [id]);
      await log("Reopened", `By ${u.name}`);
    }
    await q("INSERT INTO audit_log (user_id, action, target) VALUES ($1,$2,$3)", [u.uid, `incident.${v.action}`, id]);
    return { ok: true };
  });
});
