import { route } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { clampLimit, listAutomationRuns } from "@/lib/server/automations";

export const dynamic = "force-dynamic";

export const GET = route<{ id: string }>(async (req, { params }) => {
  await requireSession();
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw new HttpError(400, "err.badId");
  return listAutomationRuns(id, clampLimit(new URL(req.url).searchParams.get("limit"), 50));
});
