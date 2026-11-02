import { route } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
import { clampLimit, listRuns } from "@/lib/server/automations";

export const dynamic = "force-dynamic";

/** Activity: the latest firings, tests and failures across every alert, newest first. Anyone signed in can read it. */
export const GET = route(async (req) => {
  await requireSession();
  return listRuns(clampLimit(new URL(req.url).searchParams.get("limit"), 100));
});
