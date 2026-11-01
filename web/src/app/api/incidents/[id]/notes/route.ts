import { z } from "zod";
import { route, body } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
import { query } from "@/lib/server/db";
export const POST = route<{ id: string }>(async (req, { params }) => {
  const u = await requireSession();
  const { text } = await body(req, z.object({ text: z.string().trim().min(1).max(1000) }));
  await query("INSERT INTO incident_timeline (incident_id, kind, title, detail, user_id) VALUES ($1,'note','Note',$2,$3)", [(await params).id, text, u.uid]);
  return { ok: true };
});
