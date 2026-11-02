import { NextResponse, type NextRequest } from "next/server";
import { verifySession, COOKIE } from "@/lib/server/session";
import { can } from "@/lib/roles";
import { tFromRequest } from "@/lib/i18n";

const PUBLIC = ["/login", "/api/auth/login", "/api/health"];

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (PUBLIC.includes(pathname)) return NextResponse.next();
  const s = await verifySession(req.cookies.get(COOKIE)?.value);
  if (!s) {
    if (pathname.startsWith("/api/")) return NextResponse.json({ error: tFromRequest(req)("err.notSignedIn"), code: "err.notSignedIn" }, { status: 401 });
    const url = new URL("/login", req.url);
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  if (!can(s.role, pathname)) {
    if (pathname.startsWith("/api/")) return NextResponse.json({ error: tFromRequest(req)("err.notAllowedForYourRole"), code: "err.notAllowedForYourRole" }, { status: 403 });
    return NextResponse.redirect(new URL("/", req.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico|brand/).*)"] };
