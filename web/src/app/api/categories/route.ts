import { route } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
import { listBehaviorRules, listCategories } from "@/lib/server/queries";
export const dynamic = "force-dynamic";
export const GET = route(async () => { await requireSession(); const [categories, rules] = await Promise.all([listCategories(), listBehaviorRules()]);
  return { categories, rules }; });
