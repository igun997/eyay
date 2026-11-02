import { route } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { one } from "@/lib/server/db";

export const GET = route<{ id: string; idx: string }>(async (_r, { params }) => {
  await requireSession();
  const { id, idx } = await params;
  const r = await one<{ jpeg: Buffer }>("SELECT jpeg FROM event_snapshots WHERE event_id=$1 AND idx=$2", [id, Number(idx) || 0]);
  if (!r) throw new HttpError(404, "err.noSnapshot");
  return new Response(new Uint8Array(r.jpeg), { headers: { "Content-Type": "image/jpeg", "Cache-Control": "private, max-age=3600" } });
});
