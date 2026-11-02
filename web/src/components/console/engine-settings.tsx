"use client";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { StatusChip } from "@/components/console/status-chip";
import { CalibrationEditor } from "@/components/console/calibration-editor";
import { api } from "@/hooks/use-api";
import { BUILTIN, FIELD_LABELS, SEVERITIES, checkEffective, compact, effective, mergeConfig, parseConfig, type EngineConfig } from "@/lib/engine-config";
import type { SourceRow } from "@/lib/types";

type Group = "detector" | "tracker" | "events" | "calibration" | "classifier";
type NumField = { group: Group; key: string; label: string; hint: string; min: number; max: number; step: number };

const NUM_FIELDS: NumField[] = [
  { group: "detector", key: "nms_iou", label: "Merge overlapping boxes", hint: "Boxes of the same kind that overlap more than this are treated as one object.", min: 0.1, max: 0.9, step: 0.05 },
  { group: "detector", key: "target_fps", label: "Checks per second", hint: "How many pictures per second the camera AI looks at. Lower saves computer power; higher catches fast objects.", min: 0.5, max: 15, step: 0.5 },
  { group: "detector", key: "intra_threads", label: "Processing power (0 = automatic)", hint: "How many processor threads this camera may use. 0 lets the system decide. Changing it briefly reconnects the camera.", min: 0, max: 32, step: 1 },
  { group: "tracker", key: "iou_threshold", label: "How strictly to follow the same object", hint: "Higher: an object has to stay close to where it was to count as the same one.", min: 0.05, max: 0.9, step: 0.05 },
  { group: "tracker", key: "max_age", label: "How long to remember an object that disappears (checks)", hint: "How many checks an object is remembered after it is lost from view.", min: 0, max: 60, step: 1 },
  { group: "tracker", key: "min_hits", label: "How many looks before it counts", hint: "An object must be seen this many times before it can create an event. This ignores one-off flickers.", min: 1, max: 10, step: 1 },
  { group: "events", key: "new_track_gap_s", label: "Quiet-period exception for a new object (seconds)", hint: "0 = off. An object that has not been reported yet may still create an event this many seconds after the last one, even during the quiet period. 5-10 s reports a second person who arrives soon after the first, at the price of more events when the same person is mistaken for a new one.", min: 0, max: 300, step: 1 },
  { group: "classifier", key: "rule_frames", label: "Follow-up photos to check", hint: "Warning-sign questions look at the first few follow-up photos; the highest answer counts.", min: 1, max: 6, step: 1 },
  { group: "classifier", key: "rule_max_queue", label: "Skip questions when this many events are waiting", hint: "While more events than this wait for the second AI check, warning-sign questions are skipped.", min: 0, max: 200, step: 1 },
  { group: "calibration", key: "speed_limit_kmh", label: "Speed limit (km/h)", hint: "Anything faster gets the warning sign “Over speed limit”, for a person to review.", min: 5, max: 300, step: 5 },
];

const show = (v: unknown) => (v === undefined || v === null ? "automatic" : Array.isArray(v) ? `${v.length} points` : String(v));
const FROM = { camera: "this camera", defaults: "the settings for all cameras", builtin: "the standard value", unset: "" } as const;

/** One row: shows what is inherited, lets the admin override, and reset back to inheriting. */
function Row({ label, hint, from, inheritedText, overridden, onReset, children }: { label: string; hint?: string; from: keyof typeof FROM; inheritedText: string; overridden: boolean; onReset: () => void; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[1fr_190px] items-start gap-4 border-b border-border py-3 last:border-0">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="text-xs text-text-2">{hint}</p>}
        <p className="mt-1 font-mono text-[11px] text-text-3">{overridden ? <>otherwise <span className="text-text-2">{inheritedText}</span></> : <>uses <span className="text-text-2">{inheritedText}</span>{from !== "unset" && <> · from {FROM[from]}</>}</>}</p>
      </div>
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">{children}</div>
        <Button type="button" variant="ghost" size="icon" aria-label={`Reset ${label} to the shared value`} disabled={!overridden} onClick={onReset} className="size-8"><RotateCcw className="size-3.5" aria-hidden /></Button>
      </div>
    </div>
  );
}

/** The editable form for one scope: a camera (overrides over the defaults) or the defaults themselves (over the built-in values). */
export function EngineConfigForm({ scope, draft, setDraft, defaults, source, onEditGeometry }: {
  scope: "camera" | "defaults"; draft: EngineConfig; setDraft: (c: EngineConfig) => void; defaults: EngineConfig; source?: SourceRow; onEditGeometry?: () => void;
}) {
  const above = scope === "camera" ? defaults : undefined; // what this scope inherits from
  const put = (group: Group, key: string, v: unknown) => setDraft({ ...draft, [group]: { ...(draft[group] as object | undefined), [key]: v } } as EngineConfig);
  const cur = (group: Group, key: string) => (draft[group] as Record<string, unknown> | undefined)?.[key];
  const inh = (group: Group, key: string) => effective(group, key, above, undefined);
  const cal = { ...(draft.calibration ?? {}) };
  const method = cal.method ?? (inh("calibration", "method").value as string);

  return (
    <div className="space-y-5">
      {(["detector", "tracker", "events"] as const).map((g) => (
        <section key={g}>
          <h3 className="caps border-b border-border pb-2">{g === "detector" ? "Camera AI" : g === "tracker" ? "Following objects" : "Events"}</h3>
          {NUM_FIELDS.filter((f) => f.group === g).map((f) => {
            const i = inh(f.group, f.key), v = cur(f.group, f.key);
            return (
              <Row key={f.key} label={f.label} hint={f.hint} from={i.from} inheritedText={show(i.value)} overridden={v != null} onReset={() => put(f.group, f.key, null)}>
                <Input aria-label={f.label} type="number" min={f.min} max={f.max} step={f.step} value={(v as number | null | undefined) ?? ""} placeholder={show(i.value)}
                  onChange={(e) => put(f.group, f.key, e.target.value === "" ? null : Number(e.target.value))} className="h-8 border-border-strong bg-background font-mono" />
              </Row>
            );
          })}
        </section>
      ))}

      <section>
        <h3 className="caps border-b border-border pb-2">Speed setup</h3>
        {NUM_FIELDS.filter((f) => f.group === "calibration").map((f) => {
          const i = inh(f.group, f.key), v = cur(f.group, f.key);
          return (
            <Row key={f.key} label={f.label} hint={f.hint} from={i.from} inheritedText={show(i.value)} overridden={v != null} onReset={() => put(f.group, f.key, null)}>
              <Input aria-label={f.label} type="number" min={f.min} max={f.max} step={f.step} value={(v as number | null | undefined) ?? ""} placeholder={show(i.value)}
                onChange={(e) => put(f.group, f.key, e.target.value === "" ? null : Number(e.target.value))} className="h-8 border-border-strong bg-background font-mono" />
            </Row>
          );
        })}
        {(() => { const i = inh("calibration", "speed_severity"), v = cur("calibration", "speed_severity"); return (
          <Row label="How serious an over-limit warning is" hint="Severity of the warning sign “Over speed limit”." from={i.from} inheritedText={show(i.value)} overridden={v != null} onReset={() => put("calibration", "speed_severity", null)}>
            <select aria-label="How serious an over-limit warning is" value={(v as string | null | undefined) ?? ""} onChange={(e) => put("calibration", "speed_severity", e.target.value || null)} className="h-8 w-full rounded-sm border border-border-strong bg-background px-2 text-sm">
              <option value="">Use the shared value</option>{SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Row>); })()}
        {(() => { const i = inh("calibration", "show_geometry"), v = cur("calibration", "show_geometry"); return (
          <Row label="Show the lines on the live view" hint="Turn off once the lines are in the right place." from={i.from} inheritedText={show(i.value)} overridden={v != null} onReset={() => put("calibration", "show_geometry", null)}>
            <Switch aria-label="Show the lines on the live view" checked={(v as boolean | undefined) ?? (i.value as boolean | undefined) ?? true} onCheckedChange={(c) => put("calibration", "show_geometry", c)} />
          </Row>); })()}
        {scope === "camera" && source && (
          <div className="flex items-center justify-between gap-4 py-3">
            <div><p className="text-sm font-medium">Speed measurement</p>
              <p className="text-xs text-text-2">{method === "line" ? `Two lines · ${cal.line_axis === "y" ? "up-down" : "left-right"} motion · ${cal.distance_m ?? "?"} m apart` : method === "homography" ? "Angled view (four corners)" : "Off: no speeds are estimated"}</p></div>
            <Button type="button" variant="outline" onClick={onEditGeometry}>Set up on a camera picture</Button>
          </div>
        )}
        {scope === "defaults" && <p className="py-3 text-xs text-text-2">The lines and corners belong to one camera: set them in that camera’s settings.</p>}
      </section>

      {scope === "defaults" && (
        <section>
          <h3 className="caps border-b border-border pb-2">Second AI check <StatusChip className="ml-2 align-middle">ALL CAMERAS</StatusChip></h3>
          <p className="py-2 text-xs text-text-2">One second AI check serves every camera, so these cannot be set per camera. Empty fields use the installation’s standard values. Applied within 30 seconds.</p>
          {(() => { const i = inh("classifier", "rules_enabled"), v = cur("classifier", "rules_enabled"); return (
            <Row label="Warning-sign questions" hint="Off: the second AI check only says person, vehicle or not a real detection, and asks no warning-sign questions." from={i.from} inheritedText={show(i.value)} overridden={v != null} onReset={() => put("classifier", "rules_enabled", null)}>
              <Switch aria-label="Warning-sign questions" checked={(v as boolean | undefined) ?? (i.value as boolean | undefined) ?? true} onCheckedChange={(c) => put("classifier", "rules_enabled", c)} />
            </Row>); })()}
          {NUM_FIELDS.filter((f) => f.group === "classifier").map((f) => {
            const i = inh(f.group, f.key), v = cur(f.group, f.key);
            return (
              <Row key={f.key} label={f.label} hint={f.hint} from={i.from} inheritedText={show(i.value)} overridden={v != null} onReset={() => put(f.group, f.key, null)}>
                <Input aria-label={f.label} type="number" min={f.min} max={f.max} step={f.step} value={(v as number | null | undefined) ?? ""} placeholder={show(i.value)}
                  onChange={(e) => put(f.group, f.key, e.target.value === "" ? null : Number(e.target.value))} className="h-8 border-border-strong bg-background font-mono" />
              </Row>
            );
          })}
          {(() => { const i = inh("classifier", "batch"), v = cur("classifier", "batch"); return (
            <Row label="Ask all questions at once" hint="On is faster. Some small local setups of the second AI check crash when asked several questions at once: leave it off unless it runs on a server you have tested." from={i.from} inheritedText={show(i.value)} overridden={v != null} onReset={() => put("classifier", "batch", null)}>
              <Switch aria-label="Ask all questions at once" checked={(v as boolean | undefined) ?? (i.value as boolean | undefined) ?? false} onCheckedChange={(c) => put("classifier", "batch", c)} />
            </Row>); })()}
        </section>
      )}
    </div>
  );
}

const lines = (c: EngineConfig) => Object.values(compact(c)).reduce((n, g) => n + Object.keys(g as object).length, 0);

/** Per-camera dialog. Saving stores only real overrides; the worker applies them within a poll (about 5 s). */
export function EngineSettingsDialog({ open, onOpenChange, source, defaults, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; source: SourceRow; defaults: EngineConfig; onSaved: () => void;
}) {
  const [draft, setDraft] = useState<EngineConfig>(() => compact(source.config));
  const [geom, setGeom] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problems = useMemo(() => {
    const parsed = parseConfig(draft);
    return parsed.ok ? checkEffective(mergeConfig(defaults, parsed.config)) : parsed.errors;
  }, [draft, defaults]);

  async function save() {
    setBusy(true); setErr(null);
    try {
      await api(`/api/sources/${source.id}/config`, { method: "PUT", json: compact(draft) });
      toast.success(`${source.code} settings saved`, { description: "Applied within about 5 seconds. Changing processing power briefly reconnects the camera." });
      onSaved(); onOpenChange(false);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <>
      <Dialog open={open && !geom} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[92vh] sm:max-w-2xl overflow-y-auto rounded-lg border-border bg-surface-2">
          <DialogHeader>
            <DialogTitle>Camera AI settings · {source.code}</DialogTitle>
            <DialogDescription>Each field uses the settings for all cameras until you change it here. Reset a field to use the shared value again.</DialogDescription>
          </DialogHeader>
          <EngineConfigForm scope="camera" draft={draft} setDraft={setDraft} defaults={defaults} source={source} onEditGeometry={() => setGeom(true)} />
          {(err || problems.length > 0) && <ul role="alert" className="space-y-1 rounded-sm border border-sev-critical/40 bg-sev-critical/10 p-3 text-sm text-sev-critical">{[...(err ? [err] : []), ...problems].map((p) => <li key={p}>{p}</li>)}</ul>}
          <DialogFooter className="items-center sm:justify-between">
            <span className="font-mono text-xs text-text-3">{lines(draft)} setting{lines(draft) === 1 ? "" : "s"} changed for this camera</span>
            <span className="flex gap-2"><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button type="button" disabled={busy || problems.length > 0} onClick={save}>Save settings</Button></span>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <CalibrationEditor open={open && geom} onOpenChange={(o) => setGeom(o)} source={source} value={draft.calibration ?? {}}
        inherited={{ ...BUILTIN.calibration, ...(defaults.calibration ?? {}) }} onApply={(c) => setDraft({ ...draft, calibration: { ...(draft.calibration ?? {}), ...c } })} />
    </>
  );
}

/** Global defaults dialog (admin): applies to every camera that does not override the field. */
export function EngineDefaultsDialog({ open, onOpenChange, defaults, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; defaults: EngineConfig; onSaved: () => void }) {
  const [draft, setDraft] = useState<EngineConfig>(() => compact(defaults));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problems = useMemo(() => { const p = parseConfig(draft, "defaults"); return p.ok ? checkEffective(p.config) : p.errors; }, [draft]);
  async function save() {
    setBusy(true); setErr(null);
    try {
      await api("/api/settings/engine", { method: "PUT", json: compact(draft) });
      toast.success("Settings for all cameras saved", { description: "Cameras without their own value pick them up within about 5 seconds." });
      onSaved(); onOpenChange(false);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] sm:max-w-2xl overflow-y-auto rounded-lg border-border bg-surface-2">
        <DialogHeader>
          <DialogTitle>Settings for all cameras <StatusChip className="ml-2 align-middle">SHARED</StatusChip></DialogTitle>
          <DialogDescription>Used by every camera that does not have its own value. An empty field uses the standard value.</DialogDescription>
        </DialogHeader>
        <EngineConfigForm scope="defaults" draft={draft} setDraft={setDraft} defaults={{}} />
        {(err || problems.length > 0) && <ul role="alert" className="space-y-1 rounded-sm border border-sev-critical/40 bg-sev-critical/10 p-3 text-sm text-sev-critical">{[...(err ? [err] : []), ...problems].map((p) => <li key={p}>{p}</li>)}</ul>}
        <DialogFooter><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button type="button" disabled={busy || problems.length > 0} onClick={save}>Save for all cameras</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Compact summary shown on the Sources page: what this camera overrides. */
export function overrideSummary(c: EngineConfig | undefined): string[] {
  const out: string[] = [];
  for (const [g, leaves] of Object.entries(compact(c))) for (const [k, v] of Object.entries(leaves as object)) out.push(`${FIELD_LABELS[`${g}.${k}`] ?? k}: ${Array.isArray(v) ? `${v.length} points` : String(v)}`);
  return out;
}
