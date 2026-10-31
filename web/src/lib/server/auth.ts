import { cookies } from "next/headers";
import bcrypt from "bcryptjs";
import { one } from "./db";
import { COOKIE, verifySession, type Session } from "./session";
export { COOKIE, signSession, type Session } from "./session";
import "server-only";
import type { Role } from "../roles";


export async function getSession() {
  return verifySession((await cookies()).get(COOKIE)?.value);
}

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** For route handlers: throws HttpError(401/403). */
export async function requireSession(roles?: Role[]) {
  const s = await getSession();
  if (!s) throw new HttpError(401, "Not signed in");
  if (roles && !roles.includes(s.role)) throw new HttpError(403, "Not allowed for your role");
  return s;
}

export async function authenticate(email: string, password: string): Promise<Session | null> {
  const u = await one<{ id: string; email: string; name: string; role: Role; password_hash: string; active: boolean }>(
    "SELECT id,email,name,role,password_hash,active FROM users WHERE lower(email)=lower($1)",
    [email],
  );
  if (!u || !u.active || !(await bcrypt.compare(password, u.password_hash))) return null;
  return { uid: u.id, email: u.email, name: u.name, role: u.role };
}

export const hash = (p: string) => bcrypt.hash(p, 10);
