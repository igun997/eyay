import { NextResponse } from "next/server";
import { COOKIE } from "@/lib/server/session";
export async function POST() {
  const res = NextResponse.json({ ok: true });
  res.cookies.delete(COOKIE);
  return res;
}
