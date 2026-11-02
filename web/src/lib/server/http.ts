import { NextResponse } from "next/server";
import { ZodError, type ZodType } from "zod";
import { langFromRequest, makeT, translate, isMessageKey } from "@/lib/i18n";
import { issueLine } from "@/lib/i18n/validation";
import { HttpError } from "./auth";

type Ctx<P> = { params: Promise<P> };

/**
 * Wraps a route handler: maps HttpError/ZodError/pg unique violations to JSON errors in the language of the request (cookie
 * `eyeai_lang`). The stable `code` is the catalog key of the message (or "validation"), for programmatic use.
 */
export function route<P = Record<string, never>>(fn: (req: Request, ctx: Ctx<P>) => Promise<Response | unknown>) {
  return async (req: Request, ctx: Ctx<P>) => {
    try {
      const out = await fn(req, ctx);
      return out instanceof Response ? out : NextResponse.json(out);
    } catch (e) {
      const lang = langFromRequest(req);
      const t = makeT(lang);
      if (e instanceof HttpError) {
        return NextResponse.json({ error: translate(lang, e.message, e.params), code: isMessageKey(e.message) ? e.message : undefined }, { status: e.status });
      }
      if (e instanceof ZodError) return NextResponse.json({ error: e.issues.map((i) => issueLine(t, i)).join("; "), code: "validation" }, { status: 400 });
      if ((e as { code?: string }).code === "23505") return NextResponse.json({ error: t("err.duplicate"), code: "err.duplicate" }, { status: 409 });
      console.error(e);
      return NextResponse.json({ error: t("err.internal"), code: "err.internal" }, { status: 500 });
    }
  };
}

export async function body<T>(req: Request, schema: ZodType<T>): Promise<T> {
  return schema.parse(await req.json().catch(() => ({})));
}
