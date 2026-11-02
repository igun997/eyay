import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { tx } from "@/lib/server/db";
import { incidentAction } from "@/lib/schemas";
import { tFromRequest } from "@/lib/i18n";

export const POST = route<{ id: string }>(async (req, { params }) => {
  const u = await requireSession();
  const { id } = await params;
  const v = await body(req, incidentAction);
  const tr = tFromRequest(req);
  return tx(async (q) => {
    const [inc] = await q<{ id: string; status: string }>("SELECT id, status FROM incidents WHERE id=$1 FOR UPDATE", [id]);
    if (!inc) throw new HttpError(404, "err.incidentNotFound");
    const log = (title: string, detail?: string) =>
      q("INSERT INTO incident_timeline (incident_id, kind, title, detail, user_id) VALUES ($1,'operator',$2,$3,$4)", [id, title, detail ?? null, u.uid]);
    if (v.action === "acknowledge") {
      await q("UPDATE incidents SET status='acknowledged' WHERE id=$1 AND status='open'", [id]);
      await log(tr("timeline.acknowledged"));
    } else if (v.action === "assign") {
      let who = "";
      if (v.userId) {
        const [target] = await q<{ name: string }>("SELECT name FROM users WHERE id=$1 AND active", [v.userId]);
        if (!target) throw new HttpError(400, "err.userNotFound");
        who = target.name;
      }
      await q("UPDATE incidents SET assigned_to=$2 WHERE id=$1", [id, v.userId]);
      await log(v.userId ? tr("timeline.assignedTo", { name: who }) : tr("timeline.unassigned"), tr("timeline.by", { name: u.name }));
    } else if (v.action === "resolve") {
      await q("UPDATE incidents SET status='resolved', resolved_at=now(), resolved_by=$2 WHERE id=$1", [id, u.uid]);
      await log(tr("timeline.resolved"), tr("timeline.by", { name: u.name }));
    } else {
      await q("UPDATE incidents SET status='open', resolved_at=NULL, resolved_by=NULL WHERE id=$1", [id]);
      await log(tr("timeline.reopened"), tr("timeline.by", { name: u.name }));
    }
    await q("INSERT INTO audit_log (user_id, action, target) VALUES ($1,$2,$3)", [u.uid, `incident.${v.action}`, id]);
    return { ok: true };
  });
});
