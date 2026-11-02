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
import { BUILTIN, SEVERITIES, checkEffective, compact, effective, mergeConfig, parseConfig, type EngineConfig } from "@/lib/engine-config";
import type { SourceRow } from "@/lib/types";

type Group = "detector" | "tracker" | "calibration";
type NumField = { group: Group; key: string; label: string; hint: string; min: number; max: number; step: number };

const NUM_FIELDS: NumField[] = [
  { group: "detector", key: "nms_iou", label: "Duplicate-box overlap (NMS IoU)", hint: "Same-class boxes above this overlap collapse into one.", min: 0.1, max: 0.9, step: 0.05 },
  { group: "detector", key: "target_fps", label: "Analysed frames per second", hint: "Lower saves CPU, higher catches fast objects.", min: 0.5, max: 15, step: 0.5 },
  { group: "detector", key: "intra_threads", label: "ONNX threads", hint: "0 lets the runtime decide. Changing it reconnects the stream.", min: 0, max: 32, step: 1 },
  { group: "tracker", key: "iou_threshold", label: "Track match overlap (IoU)", hint: "Minimum overlap to keep the same track id.", min: 0.05, max: 0.9, step: 0.05 },
  { group: "tracker", key: "max_age", label: "Track max age (frames)", hint: "Analysed frames a track survives without a match.", min: 0, max: 60, step: 1 },
  { group: "tracker", key: "min_hits", label: "Frames before an event", hint: "A track must be seen this many times; drops one-frame flicker.", min: 1, max: 10, step: 1 },
  { group: "calibration", key: "speed_limit_kmh", label: "Speed limit (km/h)", hint: "Faster tracks are flagged “Over speed limit” for review.", min: 5, max: 300, step: 5 },
];

const show = (v: unknown) => (v === undefined || v === null ? "worker decides" : Array.isArray(v) ? `${v.length} points` : String(v));
const FROM = { camera: "this camera", defaults: "defaults", builtin: "built-in", unset: "" } as const;

/** One row: shows what is inherited, lets the admin override, and reset back to inheriting. */
function Row({ label, hint, from, inheritedText, overridden, onReset, children }: { label: string; hint?: string; from: keyof typeof FROM; inheritedText: string; overridden: boolean; onReset: () => void; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[1fr_190px] items-start gap-4 border-b border-border py-3 last:border-0">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="text-xs text-text-2">{hint}</p>}
        <p className="mt-1 font-mono text-[11px] text-text-3">{overridden ? <>inherited would be <span className="text-text-2">{inheritedText}</span></> : <>inherits <span className="text-text-2">{inheritedText}</span>{from !== "unset" && <> · from {FROM[from]}</>}</>}</p>
      </div>
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">{children}</div>
        <Button type="button" variant="ghost" size="icon" aria-label={`Reset ${label} to inherit`} disabled={!overridden} onClick={onReset} className="size-8"><RotateCcw className="size-3.5" aria-hidden /></Button>
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
      {(["detector", "tracker"] as const).map((g) => (
        <section key={g}>
          <h3 className="caps border-b border-border pb-2">{g === "detector" ? "Detector" : "Tracker"}</h3>
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
        <h3 className="caps border-b border-border pb-2">Speed</h3>
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
          <Row label="Over-limit indicator severity" hint="Severity of the “Over speed limit” indicator." from={i.from} inheritedText={show(i.value)} overridden={v != null} onReset={() => put("calibration", "speed_severity", null)}>
            <select aria-label="Over-limit indicator severity" value={(v as string | null | undefined) ?? ""} onChange={(e) => put("calibration", "speed_severity", e.target.value || null)} className="h-8 w-full rounded-sm border border-border-strong bg-background px-2 text-sm">
              <option value="">inherit</option>{SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Row>); })()}
        {(() => { const i = inh("calibration", "show_geometry"), v = cur("calibration", "show_geometry"); return (
          <Row label="Draw the geometry on the live view" hint="Turn off once the lines are tuned." from={i.from} inheritedText={show(i.value)} overridden={v != null} onReset={() => put("calibration", "show_geometry", null)}>
            <Switch aria-label="Draw the geometry on the live view" checked={(v as boolean | undefined) ?? (i.value as boolean | undefined) ?? true} onCheckedChange={(c) => put("calibration", "show_geometry", c)} />
          </Row>); })()}
        {scope === "camera" && source && (
          <div className="flex items-center justify-between gap-4 py-3">
            <div><p className="text-sm font-medium">Measurement</p>
              <p className="text-xs text-text-2">{method === "line" ? `Two lines · ${cal.line_axis === "y" ? "up-down" : "left-right"} motion · ${cal.distance_m ?? "?"} m apart` : method === "homography" ? "Four road corners" : "Off: no speeds are estimated"}</p></div>
            <Button type="button" variant="outline" onClick={onEditGeometry}>Edit on snapshot</Button>
          </div>
        )}
        {scope === "defaults" && <p className="py-3 text-xs text-text-2">Lines and road corners belong to one camera: set them from that camera’s engine settings.</p>}
      </section>
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
      toast.success(`${source.code} engine settings saved`, { description: "The worker applies them within about 5 seconds. Changing ONNX threads reconnects the stream." });
      onSaved(); onOpenChange(false);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <>
      <Dialog open={open && !geom} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto rounded-lg border-border bg-surface-2">
          <DialogHeader>
            <DialogTitle>Engine settings · {source.code}</DialogTitle>
            <DialogDescription>Each field inherits the defaults until you override it. Reset a field to inherit again.</DialogDescription>
          </DialogHeader>
          <EngineConfigForm scope="camera" draft={draft} setDraft={setDraft} defaults={defaults} source={source} onEditGeometry={() => setGeom(true)} />
          {(err || problems.length > 0) && <ul role="alert" className="space-y-1 rounded-sm border border-sev-critical/40 bg-sev-critical/10 p-3 text-sm text-sev-critical">{[...(err ? [err] : []), ...problems].map((p) => <li key={p}>{p}</li>)}</ul>}
          <DialogFooter className="items-center sm:justify-between">
            <span className="font-mono text-xs text-text-3">{lines(draft)} override{lines(draft) === 1 ? "" : "s"}</span>
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
  const problems = useMemo(() => { const p = parseConfig(draft); return p.ok ? checkEffective(p.config) : p.errors; }, [draft]);
  async function save() {
    setBusy(true); setErr(null);
    try {
      await api("/api/settings/engine", { method: "PUT", json: compact(draft) });
      toast.success("Engine defaults saved", { description: "Cameras that do not override a field pick it up within about 5 seconds." });
      onSaved(); onOpenChange(false);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto rounded-lg border-border bg-surface-2">
        <DialogHeader>
          <DialogTitle>Engine defaults <StatusChip className="ml-2 align-middle">ALL CAMERAS</StatusChip></DialogTitle>
          <DialogDescription>Applies to every camera that does not override the field. An empty field uses the worker’s built-in value.</DialogDescription>
        </DialogHeader>
        <EngineConfigForm scope="defaults" draft={draft} setDraft={setDraft} defaults={{}} />
        {(err || problems.length > 0) && <ul role="alert" className="space-y-1 rounded-sm border border-sev-critical/40 bg-sev-critical/10 p-3 text-sm text-sev-critical">{[...(err ? [err] : []), ...problems].map((p) => <li key={p}>{p}</li>)}</ul>}
        <DialogFooter><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button type="button" disabled={busy || problems.length > 0} onClick={save}>Save defaults</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Compact summary shown on the Sources page: what this camera overrides. */
export function overrideSummary(c: EngineConfig | undefined): string[] {
  const out: string[] = [];
  for (const [g, leaves] of Object.entries(compact(c))) for (const [k, v] of Object.entries(leaves as object)) out.push(`${g}.${k} = ${Array.isArray(v) ? `${v.length} points` : String(v)}`);
  return out;
}
