import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { deleteBehaviorRule, listCategories, updateBehaviorRule } from "@/lib/server/queries";
import { query } from "@/lib/server/db";
import { normaliseRule, ruleKey, ruleUpdate } from "@/lib/schemas";

export const PUT = route<{ key: string }>(async (req, { params }) => {
  const u = await requireSession(["admin"]);
  const key = ruleKey.parse((await params).key);
  const v = normaliseRule(await body(req, ruleUpdate));
  const known = new Set((await listCategories()).map((c) => c.label));
  const bad = v.applies_to.filter((l) => !known.has(l));
  if (bad.length) throw new HttpError(400, "err.unknownCategories", { list: bad.join(", ") });
  const row = await updateBehaviorRule(key, { ...v, question: v.question ?? null });
  if (!row) throw new HttpError(404, "err.ruleNotFound");
  await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'rule.update',$2)", [u.uid, key]);
  return row;
});

export const DELETE = route<{ key: string }>(async (_req, { params }) => {
  const u = await requireSession(["admin"]);
  const key = ruleKey.parse((await params).key);
  if (!(await deleteBehaviorRule(key))) throw new HttpError(404, "err.ruleNotFound");
  await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'rule.delete',$2)", [u.uid, key]);
  return { ok: true };
});
