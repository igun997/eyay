import { z } from "zod";
import { route, body } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
import { listSources, maskCreds } from "@/lib/server/queries";
import { one, query } from "@/lib/server/db";
import { sourceInput } from "@/lib/schemas";

export const dynamic = "force-dynamic";
export const GET = route(async () => {
  const s = await requireSession();
  const rows = await listSources();
  return s.role === "admin" ? rows : rows.map(maskCreds);
});

export const POST = route(async (req) => {
  const s = await requireSession(["admin"]);
  const v = await body(req, sourceInput);
  const row = await one<{ id: string }>(
    `INSERT INTO sources (code, name, url, kind, labels, min_confidence, enabled, created_by)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id`,
    [v.code, v.name, v.url, v.kind, v.labels, v.min_confidence, v.enabled, s.uid],
  );
  await query("INSERT INTO audit_log (user_id, action, target, detail) VALUES ($1,'source.create',$2,$3)", [s.uid, v.code, JSON.stringify({ kind: v.kind })]);
  return row;
});
