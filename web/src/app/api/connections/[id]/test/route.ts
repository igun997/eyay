import { route } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { getConnection, queueRun } from "@/lib/server/automations";
import { query } from "@/lib/server/db";

/** Queue a connection check; the analysis service picks it up within about a second (poll /api/automation-runs). */
export const POST = route<{ id: string }>(async (_req, { params }) => {
  const u = await requireSession(["admin"]);
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw new HttpError(400, "Bad id");
  const c = await getConnection(id);
  if (!c) throw new HttpError(404, "Connection not found");
  const run = await queueRun({ automationId: null, connectionId: id, mode: "test_send", userId: u.uid });
  await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'connection.test',$2)", [u.uid, c.name]);
  return { ok: true, run_id: Number(run!.id), status: "queued" };
});
