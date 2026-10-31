import { route } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
import { listIncidents } from "@/lib/server/queries";
export const dynamic = "force-dynamic";
export const GET = route(async (req) => {
  await requireSession();
  const s = new URL(req.url).searchParams.get("status");
  return listIncidents(s === "active" || s === "resolved" ? s : undefined);
});
