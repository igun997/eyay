import { route } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
import { listEvents, eventCounts } from "@/lib/server/queries";
export const dynamic = "force-dynamic";

export const GET = route(async (req) => {
  await requireSession();
  const u = new URL(req.url).searchParams;
  const tab = u.get("tab");
  const [events, counts] = await Promise.all([
    listEvents({
      tab: tab === "needs_review" || tab === "resolved" ? tab : undefined,
      sourceId: u.get("source") || undefined,
      label: u.get("label") || undefined,
      severity: u.get("severity") || undefined,
      q: u.get("q") || undefined,
      sort: u.get("sort") === "oldest" ? "oldest" : "newest",
    }),
    eventCounts(),
  ]);
  return { events, counts };
});
