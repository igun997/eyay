import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { tx } from "@/lib/server/db";
import { dismissReasonKey, reviewInput } from "@/lib/schemas";
import { tFromRequest } from "@/lib/i18n";

/** Triage: the incident already exists; this only re-checks the classification (confirm / dismiss / reopen). */
export const POST = route<{ id: string }>(async (req, { params }) => {
  const u = await requireSession();
  const { id } = await params;
  const v = await body(req, reviewInput);
  const tr = tFromRequest(req);
  return tx(async (q) => {
    const [ev] = await q<{ id: string; incident_id: string | null; title: string; status: string }>(
      "SELECT id, incident_id, title, status FROM events WHERE id = $1 FOR UPDATE", [id]);
    if (!ev) throw new HttpError(404, "err.eventNotFound");
    if (v.decision === "reopen") {
      await q("UPDATE events SET status='ready_for_triage', dismiss_reason=NULL, reviewed_by=NULL, reviewed_at=NULL WHERE id=$1", [id]);
      if (ev.incident_id) await q("UPDATE incidents SET status='open', resolved_at=NULL, resolved_by=NULL WHERE id=$1 AND status='resolved'", [ev.incident_id]);
      return { ok: true };
    }
    if (ev.status !== "ready_for_triage") throw new HttpError(409, "err.alreadyReviewed");
    const status = v.decision === "confirm" ? "confirmed" : "dismissed";
    await q(
      "UPDATE events SET status=$2, dismiss_reason=$3, note=$4, reviewed_by=$5, reviewed_at=now() WHERE id=$1",
      [id, status, v.decision === "dismiss" ? v.reason : null, v.decision === "confirm" ? (v.note ?? null) : null, u.uid],
    );
    if (ev.incident_id) {
      await q(
        "INSERT INTO incident_timeline (incident_id, kind, title, detail, user_id, event_id) VALUES ($1,'triage',$2,$3,$4,$5)",
        [ev.incident_id, v.decision === "confirm" ? tr("timeline.eventConfirmed") : tr("timeline.eventDismissed"),
         v.decision === "dismiss" ? tr(dismissReasonKey[v.reason]) : (v.note ?? null), u.uid, id],
      );
      if (v.decision === "dismiss") {
        const [left] = await q<{ n: number }>(
          "SELECT count(*)::int AS n FROM events WHERE incident_id=$1 AND status <> 'dismissed'", [ev.incident_id]);
        if (left.n === 0) {
          await q("UPDATE incidents SET status='resolved', resolved_at=now(), resolved_by=$2 WHERE id=$1", [ev.incident_id, u.uid]);
          await q("INSERT INTO incident_timeline (incident_id, kind, title, user_id) VALUES ($1,'system',$3,$2)", [ev.incident_id, u.uid, tr("timeline.allDismissed")]);
        }
      }
    }
    await q("INSERT INTO audit_log (user_id, action, target, detail) VALUES ($1,$2,$3,$4)",
      [u.uid, `event.${v.decision}`, id, JSON.stringify(v)]);
    return { ok: true, status };
  });
});
