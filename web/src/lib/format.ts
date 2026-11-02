export function ago(iso: string | null | undefined, now = Date.now()) {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 10) return "now";
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d`;
}
export const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour12: false });
export const dateTime = (iso: string) => new Date(iso).toLocaleString("en-GB", { hour12: false });
export const conf = (n: number) => n.toFixed(2);
export const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** "now" / "5m ago": never "now ago". */
export const agoText = (iso: string | null | undefined) => { const a = ago(iso); return a === "now" || a === "never" ? a : `${a} ago`; };

/** Estimated speed rounded to whole km/h. */
export const formatSpeed = (kmh: number) => `${Math.round(kmh)} km/h`;
