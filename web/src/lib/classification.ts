import type { ClsLabel } from "./types";

/** Detector class (COCO) -> the second AI check's vocabulary. Bicycle is not covered by its options. */
const EDGE_TO_CLS: Record<string, ClsLabel | undefined> = { person: "person", car: "vehicle", truck: "vehicle", bus: "vehicle", motorcycle: "vehicle" };

export type Agreement = "agree" | "disagree" | "unknown";

export function agreement(edgeLabel: string, cls: ClsLabel | null): Agreement {
  const expected = EDGE_TO_CLS[edgeLabel];
  if (!cls || !expected || cls === "other") return "unknown";
  return cls === expected ? "agree" : "disagree";
}

/** The catalog key of what the AI said, for t(). */
export const clsLabelKey = (l: ClsLabel) => `label.${l}` as const;

/** `events.cls_model` is "detector" when the camera AI itself classified the event; anything else is the optional second AI check. */
export const isSecondCheck = (model: string | null | undefined) => !!model && model !== "detector";
