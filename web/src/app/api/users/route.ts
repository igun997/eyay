import { route, body } from "@/lib/server/http";
import { requireSession, hash } from "@/lib/server/auth";
import { listUsers } from "@/lib/server/queries";
import { query, one } from "@/lib/server/db";
import { userInput } from "@/lib/schemas";
export const dynamic = "force-dynamic";

export const GET = route(async () => { await requireSession(["admin"]); return listUsers(); });

export const POST = route(async (req) => {
  const a = await requireSession(["admin"]);
  const v = await body(req, userInput);
  const r = await one<{ id: string }>(
    "INSERT INTO users (email, name, password_hash, role) VALUES (lower($1),$2,$3,$4) RETURNING id",
    [v.email, v.name, await hash(v.password), v.role]);
  await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'user.create',$2)", [a.uid, v.email]);
  return r;
});
