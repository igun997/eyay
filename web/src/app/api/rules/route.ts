import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { insertBehaviorRule, listBehaviorRules, listCategories } from "@/lib/server/queries";
import { query } from "@/lib/server/db";
import { normaliseRule, ruleInput } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export const GET = route(async () => {
  await requireSession();
  return listBehaviorRules();
});

/** Behaviour rules are indicators for a person to review, never verdicts; creating one only adds a yes/no question or a burst check. */
export const POST = route(async (req) => {
  const u = await requireSession(["admin"]);
  const { key, ...v } = normaliseRule(await body(req, ruleInput));
  const known = new Set((await listCategories()).map((c) => c.label));
  const bad = v.applies_to.filter((l) => !known.has(l));
  if (bad.length) throw new HttpError(400, `applies_to: unknown categories ${bad.join(", ")}`);
  try {
    const row = await insertBehaviorRule(key, { ...v, question: v.question ?? null });
    await query("INSERT INTO audit_log (user_id, action, target) VALUES ($1,'rule.create',$2)", [u.uid, key]);
    return row;
  } catch (e) {
    if ((e as { code?: string }).code === "23505") throw new HttpError(409, `A rule with key “${key}” already exists`);
    throw e;
  }
});
