import { route } from "@/lib/server/http";
import { requireSession } from "@/lib/server/auth";
export const GET = route(async () => requireSession());
