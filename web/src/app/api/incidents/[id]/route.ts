import { route } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { getIncident, incidentTimeline, incidentEvents, listUsers } from "@/lib/server/queries";
export const dynamic = "force-dynamic";
export const GET = route<{ id: string }>(async (_r, { params }) => {
  await requireSession();
  const inc = await getIncident((await params).id);
  if (!inc) throw new HttpError(404, "Incident not found");
  const [timeline, events, users] = await Promise.all([incidentTimeline(inc.id), incidentEvents(inc.id), listUsers()]);
  return { incident: inc, timeline, events, assignees: users.filter((u) => u.active).map((u) => ({ id: u.id, name: u.name, role: u.role })) };
});
