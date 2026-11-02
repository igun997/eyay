import { z } from "zod";

/** Infers rtsp / m3u8 from the URL so the form can validate before submit. */
export function kindOf(url: string): "rtsp" | "m3u8" | null {
  if (/^rtsps?:\/\//i.test(url)) return "rtsp";
  if (/^https?:\/\/.+\.m3u8(\?.*)?$/i.test(url)) return "m3u8";
  return null;
}

export const sourceInput = z
  .object({
    code: z.string().trim().min(2).max(24).regex(/^[A-Za-z0-9_-]+$/, "v.lettersDigitsAndOnly").transform((s) => s.toUpperCase()),
    name: z.string().trim().min(1).max(80),
    url: z.string().trim().min(8).max(500),
    labels: z.array(z.string().min(1)).min(1, "v.pickAtLeastOneCategory"),
    min_confidence: z.number().min(0.05).max(0.95).default(0.4),
    enabled: z.boolean().default(true),
  })
  .superRefine((v, ctx) => {
    if (!kindOf(v.url)) ctx.addIssue({ code: "custom", path: ["url"], message: "v.useACameraAddressStarting" });
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
  password: z.string().min(8, "v.atLeast8Characters"),
  role: z.enum(["admin", "security"]),
});

export const dismissReasons = ["Shadow or lighting", "Animal", "Known person or vehicle", "Other"] as const;
/** Dismiss reasons are stored in English (stable values); this maps them to catalog keys for display. */
export const dismissReasonKey = { "Shadow or lighting": "dismiss.shadow", Animal: "dismiss.animal", "Known person or vehicle": "dismiss.known", Other: "dismiss.other" } as const;
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

// ---- behaviour rules (db/migrations/004_behavior_rules.sql) ---------------------------------------------------------
export const ruleKinds = ["classifier", "indication", "object"] as const;
export const indicationKinds = ["appeared", "moving", "lingering", "crowd"] as const;
export const severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const;

export const OBJECT_LABELS = ["knife", "scissors", "baseball bat", "bottle", "backpack", "handbag", "suitcase", "cell phone", "umbrella"] as const;
const ruleConfig = z.object({
  indication_kind: z.enum(indicationKinds), min_burst_ms: z.number().int().min(0).max(60000),
  objects: z.array(z.enum(OBJECT_LABELS)).min(1).max(OBJECT_LABELS.length), margin: z.number().min(0).max(1),
}).partial().strict();

const ruleBase = z.object({
  title: z.string().trim().min(1).max(80),
  description: z.string().trim().min(1).max(300),
  kind: z.enum(ruleKinds),
  question: z.string().trim().min(8).max(300).nullish(),
  applies_to: z.array(z.string().min(1)).min(1, "v.pickAtLeastOneCategory"),
  threshold: z.number().gt(0).max(1).default(0.75),
  config: ruleConfig.default({}),
  severity: z.enum(severities),
  opens_incident: z.boolean().default(true),
  enabled: z.boolean().default(true),
  position: z.number().int().min(0).max(1000).default(0),
});

const ruleChecks = (v: z.infer<typeof ruleBase>, ctx: z.RefinementCtx) => {
  if (v.kind === "classifier" && !v.question) ctx.addIssue({ code: "custom", path: ["question"], message: "v.aSecondAiCheckRule" });
  if (v.kind === "indication" && !v.config.indication_kind) ctx.addIssue({ code: "custom", path: ["config", "indication_kind"], message: "v.anActivityPatternRuleNeeds" });
  if (v.kind === "object" && !v.config.objects?.length) ctx.addIssue({ code: "custom", path: ["config", "objects"], message: "v.pickAtLeastOneObject" });
};

export const ruleKey = z.string().regex(/^[a-z0-9_]{2,40}$/, "v.240CharactersLowercaseLetters");
/** Create: the key is chosen once and never changes. */
export const ruleInput = ruleBase.extend({ key: ruleKey }).superRefine(ruleChecks);
/** Update: full replacement of everything but the key (taken from the URL). */
export const ruleUpdate = ruleBase.superRefine(ruleChecks);

/** Columns that do not apply to the rule's kind are stored empty so a switched kind leaves no stale data behind. */
export const normaliseRule = <T extends z.infer<typeof ruleBase>>(v: T) => ({
  ...v,
  question: v.kind === "classifier" ? (v.question ?? null) : null,
  config:
    v.kind === "indication" ? { indication_kind: v.config.indication_kind, min_burst_ms: v.config.min_burst_ms }
    : v.kind === "object" ? { objects: v.config.objects, margin: v.config.margin ?? 0.3 }
    : {},
});

// ---- categories (docs/ENGINE_CONFIG.md "Categories") ------------------------------------------------------------------
export const categoryLabel = z.string().regex(/^[a-z][a-z0-9 _-]{0,39}$/, "v.unknownCategory");
export const categoryPatch = z
  .object({
    title: z.string().trim().min(1).max(60),
    severity: z.enum(severities),
    opens_incident: z.boolean(),
    enabled: z.boolean(),
    cooldown_s: z.number().int().min(0).max(86400),
    burst_s: z.number().int().min(0).max(60),
    burst_frames: z.number().int().min(1).max(12),
  })
  .partial()
  .strict()
  .refine((v) => Object.keys(v).length > 0, "v.nothingToUpdate");
