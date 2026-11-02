import "server-only";
import { cookies } from "next/headers";
import { LANG_COOKIE, makeT, normaliseLang, type Lang, type T } from "./index";

/** The language of the current request (cookie `eyeai_lang`, default Indonesian). */
export async function getLang(): Promise<Lang> {
  return normaliseLang((await cookies()).get(LANG_COOKIE)?.value);
}
export async function getT(): Promise<T> {
  return makeT(await getLang());
}
