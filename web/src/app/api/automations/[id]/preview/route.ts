import { route } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { getAutomation, queueRun } from "@/lib/server/automations";
import { query } from "@/lib/server/db";

/** Preview: renders what each action would send for a sample event and sends nothing. Queued for the analysis service. */
export const POST = route<{ id: string }>(async (_req, { params }) => {
  const u = await requireSession(["admin"]);
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw new HttpError(400, "Bad id");
  const a = await getAutomation(id);
  if (!a) throw new HttpError(404, "Alert not found");
  const run = await queueRun({ automationId: id, connectionId: null, mode: "test_simulate", userId: u.uid });
  await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'automation.preview',$2)", [u.uid, a.name]);
  return { ok: true, run_id: Number(run!.id), status: "queued" };
});
