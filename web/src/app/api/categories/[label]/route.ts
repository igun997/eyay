import { route, body } from "@/lib/server/http";
import { requireSession, HttpError } from "@/lib/server/auth";
import { updateCategory } from "@/lib/server/queries";
import { query } from "@/lib/server/db";
import { categoryLabel, categoryPatch } from "@/lib/schemas";

/** Edit what an event of this label means: severity, whether it opens an incident, the cooldown and the snapshot burst. Admin only. */
export const PUT = route<{ label: string }>(async (req, { params }) => {
  const u = await requireSession(["admin"]);
  const label = categoryLabel.parse(decodeURIComponent((await params).label));
  const v = await body(req, categoryPatch);
  const row = await updateCategory(label, v);
  if (!row) throw new HttpError(404, "Category not found");
  await query("INSERT INTO audit_log (user_id, action, target, detail) VALUES ($1,'category.update',$2,$3)", [u.uid, label, JSON.stringify(v)]);
  return row;
});
