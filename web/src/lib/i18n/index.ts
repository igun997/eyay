// Lightweight i18n: two catalogs (id, en), a cookie for the choice, no framework. Default language is Indonesian.
import { MESSAGES, type MessageKey } from "./messages";

export const LANGS = ["id", "en"] as const;
export type Lang = (typeof LANGS)[number];
export const DEFAULT_LANG: Lang = "id";
export const LANG_COOKIE = "eyeai_lang";
export const LANG_MAX_AGE = 60 * 60 * 24 * 365;

export type { MessageKey };
export type Params = Record<string, string | number>;
export type T = (key: MessageKey, params?: Params) => string;

/** Marks a catalog key in a module-level constant; render it with t() where it is used. */
export const K = <T extends MessageKey>(k: T): T => k;

export const normaliseLang = (v: unknown): Lang => (v === "en" || v === "id" ? v : DEFAULT_LANG);

/** The Set-Cookie style value the browser stores for a language choice (1 year, SameSite=Lax). */
export const langCookie = (lang: Lang) => `${LANG_COOKIE}=${lang}; Path=/; Max-Age=${LANG_MAX_AGE}; SameSite=Lax`;

/** What switching the language does in the browser: stores the cookie. `doc` is `document` (a fake in tests). */
export const setLangCookie = (lang: Lang, doc: { cookie: string }) => { doc.cookie = langCookie(lang); };

/** Reads the language out of a `Cookie` header (route handlers and middleware have no cookies() store). */
export function langFromCookieHeader(header: string | null | undefined): Lang {
  const m = new RegExp(`(?:^|;\\s*)${LANG_COOKIE}=([^;]+)`).exec(header ?? "");
  return normaliseLang(m?.[1]?.trim());
}

/** The two catalogs: key -> text, one per language. */
export const catalogOf = (lang: Lang): Record<string, string> => Object.fromEntries(Object.entries(MESSAGES).map(([k, v]) => [k, v[lang === "en" ? 1 : 0]]));

export const isMessageKey = (k: string): k is MessageKey => Object.prototype.hasOwnProperty.call(MESSAGES, k);

const interpolate = (text: string, params?: Params) => (params ? text.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m)) : text);

/** A key that is not in the catalog is returned as is, so a raw message passes through untouched. */
export function translate(lang: Lang, key: string, params?: Params): string {
  const entry = isMessageKey(key) ? MESSAGES[key] : undefined;
  return interpolate(entry ? entry[lang === "en" ? 1 : 0] : key, params);
}

export const makeT = (lang: Lang): T => (key, params) => translate(lang, key, params);

/** The language and translator of an incoming request (cookie `eyeai_lang`, default Indonesian). */
export const langFromRequest = (req: { headers: { get(name: string): string | null } }): Lang => langFromCookieHeader(req.headers.get("cookie"));
export const tFromRequest = (req: { headers: { get(name: string): string | null } }): T => makeT(langFromRequest(req));

/** For keys that are built at run time (enum values): checked against the catalog, falls back to the raw value. */
export const tOr = (t: T, key: string, fallback: string, params?: Params) => (isMessageKey(key) ? t(key, params) : fallback);

// ---- formatting follows the language ----------------------------------------------------------------------------------
export const localeOf = (lang: Lang) => (lang === "id" ? "id-ID" : "en-US");
const date = (iso: string | number | Date) => new Date(iso);

export const fmtDateTime = (lang: Lang, iso: string) => new Intl.DateTimeFormat(localeOf(lang), { dateStyle: "medium", timeStyle: "medium", hour12: false }).format(date(iso));
export const fmtTime = (lang: Lang, iso: string) => new Intl.DateTimeFormat(localeOf(lang), { timeStyle: "medium", hour12: false }).format(date(iso));
export const fmtDate = (lang: Lang, iso: string) => new Intl.DateTimeFormat(localeOf(lang), { dateStyle: "medium" }).format(date(iso));
export const fmtNumber = (lang: Lang, n: number, digits = 2) => new Intl.NumberFormat(localeOf(lang), { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);

/** "5 minutes ago" / "5 menit yang lalu"; under 10 seconds is "now". */
export function fmtAgo(lang: Lang, iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return translate(lang, "time.never");
  const s = Math.round((now - date(iso).getTime()) / 1000);
  if (s < 10) return translate(lang, "time.now");
  const rtf = new Intl.RelativeTimeFormat(localeOf(lang), { numeric: "always", style: "short" });
  if (s < 60) return rtf.format(-s, "second");
  const m = Math.floor(s / 60);
  if (m < 60) return rtf.format(-m, "minute");
  const h = Math.floor(m / 60);
  return h < 24 ? rtf.format(-h, "hour") : rtf.format(-Math.floor(h / 24), "day");
}
