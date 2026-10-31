import type { ClsLabel } from "./types";

/** Detector class (COCO) -> the classifier's vocabulary. Bicycle is not covered by the classifier's options. */
const EDGE_TO_CLS: Record<string, ClsLabel | undefined> = { person: "person", car: "vehicle", truck: "vehicle", bus: "vehicle", motorcycle: "vehicle" };

export type Agreement = "agree" | "disagree" | "unknown";

export function agreement(edgeLabel: string, cls: ClsLabel | null): Agreement {
  const expected = EDGE_TO_CLS[edgeLabel];
  if (!cls || !expected) return "unknown";
  return cls === expected ? "agree" : "disagree";
}

export const clsLabelText: Record<ClsLabel, string> = { person: "person", vehicle: "vehicle", false_alarm: "false alarm" };
