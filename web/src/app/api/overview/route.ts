import { route } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
import { overview } from "@/lib/server/queries";
export const dynamic = "force-dynamic";
export const GET = route(async () => { await requireSession(); return overview(); });
