// Plain-language helpers shared by the UI and its tests.

/** How easily a warning sign appears. Higher sensitivity = lower certainty needed = more warning signs (and more false ones). */
export type Sensitivity = "low" | "medium" | "high";
export const SENSITIVITY_VALUES: Record<Sensitivity, number> = { low: 0.85, medium: 0.7, high: 0.45 };
export const sensitivityOf = (threshold: number): Sensitivity => (threshold >= 0.8 ? "low" : threshold >= 0.55 ? "medium" : "high");
/** Catalog keys for t(): the level's name and its one-line help. */
export const sensitivityKey = (threshold: number) => `sensitivity.${sensitivityOf(threshold)}` as const;
export const sensitivityHelpKey = (s: Sensitivity) => `sensitivity.help.${s}` as const;

/** COCO objects the rule “spotted next to a person” can look for. */
export const OBJECT_CHOICES = ["knife", "scissors", "baseball bat", "bottle", "backpack", "handbag", "suitcase", "cell phone", "umbrella"] as const;
