import { NextResponse } from "next/server";
import { z } from "zod";
import { authenticate } from "@/lib/server/auth";
import { signSession, COOKIE } from "@/lib/server/session";
import { route, body } from "@/lib/server/http";
import { tFromRequest } from "@/lib/i18n";

export const POST = route(async (req) => {
  const { email, password } = await body(req, z.object({ email: z.string().email(), password: z.string().min(1) }));
  const s = await authenticate(email, password);
  if (!s) return NextResponse.json({ error: tFromRequest(req)("err.invalidLogin"), code: "err.invalidLogin" }, { status: 401 });
  const res = NextResponse.json({ ok: true, role: s.role });
  res.cookies.set(COOKIE, await signSession(s), { httpOnly: true, sameSite: "lax", path: "/", maxAge: 12 * 3600, secure: process.env.COOKIE_SECURE === "true" });
  return res;
});
