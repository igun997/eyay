import { CORE } from "./messages-core";
import { UI } from "./messages-ui";

/** Every user-visible string, [indonesian, english]. The two catalogs are `MESSAGES[key][0]` and `[1]`. */
export const MESSAGES = { ...UI, ...CORE } as const;
export type MessageKey = keyof typeof MESSAGES;
