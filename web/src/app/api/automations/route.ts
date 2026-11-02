import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { insertAutomation, listAutomations, listConnections } from "@/lib/server/automations";
import { query } from "@/lib/server/db";
import { automationInput, checkActionConnections } from "@/lib/automations";

export const dynamic = "force-dynamic";

export const GET = route(async () => { await requireSession(); return listAutomations(); });

/** A new alert is practice mode and switched off unless the request says otherwise; going live needs confirm_live. */
export const POST = route(async (req) => {
  const u = await requireSession(["admin"]);
  const { confirm_live, ...v } = await body(req, automationInput);
  const errs = checkActionConnections(v.actions, await listConnections());
  if (errs.length) throw new HttpError(400, errs.join("; "));
  const row = await insertAutomation(v, u.uid);
  await query("INSERT INTO audit_log (user_id, action, target, detail) VALUES ($1,'automation.create',$2,$3)", [u.uid, v.name, JSON.stringify({ mode: v.mode, confirm_live: !!confirm_live })]);
  return row;
});
