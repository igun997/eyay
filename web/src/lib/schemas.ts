import { z } from "zod";

/** Infers rtsp / m3u8 from the URL so the form can validate before submit. */
export function kindOf(url: string): "rtsp" | "m3u8" | null {
  if (/^rtsps?:\/\//i.test(url)) return "rtsp";
  if (/^https?:\/\/.+\.m3u8(\?.*)?$/i.test(url)) return "m3u8";
  return null;
}

export const sourceInput = z
  .object({
    code: z.string().trim().min(2).max(24).regex(/^[A-Za-z0-9_-]+$/, "Letters, digits, - and _ only").transform((s) => s.toUpperCase()),
    name: z.string().trim().min(1).max(80),
    url: z.string().trim().min(8).max(500),
    labels: z.array(z.string().min(1)).min(1, "Pick at least one category"),
    min_confidence: z.number().min(0.05).max(0.95).default(0.4),
    enabled: z.boolean().default(true),
  })
  .superRefine((v, ctx) => {
    if (!kindOf(v.url)) ctx.addIssue({ code: "custom", path: ["url"], message: "Use an rtsp:// URL or an http(s) URL ending in .m3u8" });
  })
  .transform((v) => ({ ...v, kind: kindOf(v.url)! }));

export const sourcePatch = z.object({
  name: z.string().trim().min(1).max(80).optional(),
  url: z.string().trim().min(8).max(500).optional(),
  labels: z.array(z.string().min(1)).min(1).optional(),
  min_confidence: z.number().min(0.05).max(0.95).optional(),
  enabled: z.boolean().optional(),
});

export const userInput = z.object({
  email: z.string().email(),
  name: z.string().trim().min(1).max(80),
  password: z.string().min(8, "At least 8 characters"),
  role: z.enum(["admin", "security"]),
});

export const dismissReasons = ["Shadow or lighting", "Animal", "Known person or vehicle", "Other"] as const;
export const reviewInput = z.discriminatedUnion("decision", [
  z.object({ decision: z.literal("confirm"), note: z.string().max(500).optional() }),
  z.object({ decision: z.literal("dismiss"), reason: z.enum(dismissReasons) }),
  z.object({ decision: z.literal("reopen") }),
]);

export const incidentAction = z.discriminatedUnion("action", [
  z.object({ action: z.literal("acknowledge") }),
  z.object({ action: z.literal("assign"), userId: z.string().uuid().nullable() }),
  z.object({ action: z.literal("resolve") }),
  z.object({ action: z.literal("reopen") }),
]);
