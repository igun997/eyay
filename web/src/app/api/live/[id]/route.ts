import { requireSession, HttpError } from "@/lib/server/auth";
import { route } from "@/lib/server/http";

export const dynamic = "force-dynamic";
const WORKER = process.env.WORKER_URL ?? "http://localhost:8090";

/** Auth-checked proxy for the worker's annotated MJPEG (boxes are drawn by the worker). */
export const GET = route<{ id: string }>(async (req, { params }) => {
  await requireSession();
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw new HttpError(400, "Bad id");
  const snapshot = new URL(req.url).searchParams.get("mode") === "snapshot";
  const up = await fetch(`${WORKER}/${snapshot ? "snapshot" : "live"}/${id}${snapshot ? ".jpg" : ".mjpg"}`, { signal: req.signal, cache: "no-store" }).catch(() => null);
  if (!up || !up.ok || !up.body) throw new HttpError(up?.status === 404 ? 404 : 502, "Stream not available");
  return new Response(up.body, { headers: { "Content-Type": up.headers.get("content-type") ?? (snapshot ? "image/jpeg" : "multipart/x-mixed-replace"), "Cache-Control": "no-store" } });
});
