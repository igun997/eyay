import { NextResponse } from "next/server";
import { one } from "@/lib/server/db";
export async function GET() {
  try { await one("SELECT 1"); return NextResponse.json({ ok: true }); }
  catch { return NextResponse.json({ ok: false }, { status: 503 }); }
}
