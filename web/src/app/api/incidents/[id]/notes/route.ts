import { z } from "zod";
import { tFromRequest } from "@/lib/i18n";
import { route, body } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
import { query } from "@/lib/server/db";
export const POST = route<{ id: string }>(async (req, { params }) => {
  const u = await requireSession();
  const { text } = await body(req, z.object({ text: z.string().trim().min(1).max(1000) }));
  await query("INSERT INTO incident_timeline (incident_id, kind, title, detail, user_id) VALUES ($1,'note',$2,$3,$4)", [(await params).id, tFromRequest(req)("timeline.note"), text, u.uid]);
  return { ok: true };
});
