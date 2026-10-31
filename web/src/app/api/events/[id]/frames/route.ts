import { route } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
import { listFrames } from "@/lib/server/queries";
export const dynamic = "force-dynamic";
export const GET = route<{ id: string }>(async (_r, { params }) => {
  await requireSession();
  return listFrames([(await params).id]);
});
