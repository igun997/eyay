// Edge-safe (used by middleware): jose only, no pg/bcrypt.
import { SignJWT, jwtVerify } from "jose";
import type { Role } from "../roles";

export const COOKIE = "eyeai_session";
const key = () => new TextEncoder().encode(process.env.AUTH_SECRET ?? "dev-only-secret-change-me-please-32b");

export type Session = { uid: string; email: string; name: string; role: Role };

export const signSession = (s: Session) =>
  new SignJWT({ ...s }).setProtectedHeader({ alg: "HS256" }).setExpirationTime("12h").sign(key());

export async function verifySession(token?: string): Promise<Session | null> {
  if (!token) return null;
  try {
    return (await jwtVerify(token, key())).payload as unknown as Session;
  } catch {
    return null;
  }
}
