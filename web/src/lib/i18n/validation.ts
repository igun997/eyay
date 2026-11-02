// Turns a zod issue into a sentence in the user's language. Custom messages in the schemas are catalog keys; zod's own are mapped by code.
import { isMessageKey, type T } from "./index";

export type IssueLike = { code: string; message: string; minimum?: unknown; maximum?: unknown; path?: PropertyKey[] };

export function issueText(t: T, i: IssueLike): string {
  if (isMessageKey(i.message)) return t(i.message);
  switch (i.code) {
    case "too_small": return t("v.tooSmall", { n: String(i.minimum) });
    case "too_big": return t("v.tooBig", { n: String(i.maximum) });
    case "invalid_type": return t("v.invalidType");
    case "invalid_value": return t("v.invalidValue");
    case "invalid_format": return t("v.invalidFormat");
    case "unrecognized_keys": return t("v.unknownKeys");
    default: return t("v.invalid");
  }
}

/** “field: message”, the field shown as written (an API path) unless a plain label is given. */
export const issueLine = (t: T, i: IssueLike, label?: string) => `${label ?? (i.path ?? []).join(".") ?? ""}${(i.path?.length || label) ? ": " : ""}${issueText(t, i)}`;
