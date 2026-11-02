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
import { BUILTIN, SEVERITIES, fieldLabel, type AutoLanguage, checkEffective, compact, effective, mergeConfig, parseConfig, type EngineConfig } from "@/lib/engine-config";
import type { SourceRow } from "@/lib/types";
import { useT } from "@/lib/i18n/client";
import { K, type MessageKey, type T } from "@/lib/i18n";

type Group = "detector" | "tracker" | "events" | "calibration" | "classifier";
type NumField = { group: Group; key: string; label: MessageKey; hint: MessageKey; min: number; max: number; step: number };

const NUM_FIELDS: NumField[] = [
  { group: "detector", key: "nms_iou", label: K("engineSettings.mergeOverlappingBoxes"), hint: K("engineSettings.boxesOfTheSameKind"), min: 0.1, max: 0.9, step: 0.05 },
  { group: "detector", key: "target_fps", label: K("engineSettings.checksPerSecond"), hint: K("engineSettings.howManyPicturesPerSecond"), min: 0.5, max: 15, step: 0.5 },
  { group: "detector", key: "intra_threads", label: K("engineSettings.processingPower0Automatic"), hint: K("engineSettings.howManyProcessorThreadsThis"), min: 0, max: 32, step: 1 },
  { group: "tracker", key: "iou_threshold", label: K("engineSettings.howStrictlyToFollowThe"), hint: K("engineSettings.higherAnObjectHasTo"), min: 0.05, max: 0.9, step: 0.05 },
  { group: "tracker", key: "max_age", label: K("engineSettings.howLongToRememberAn"), hint: K("engineSettings.howManyChecksAnObject"), min: 0, max: 60, step: 1 },
  { group: "tracker", key: "min_hits", label: K("engineSettings.howManyLooksBeforeIt"), hint: K("engineSettings.anObjectMustBeSeen"), min: 1, max: 10, step: 1 },
  { group: "events", key: "new_track_gap_s", label: K("engineSettings.quietPeriodExceptionForA"), hint: K("engineSettings.0OffAnObjectThat"), min: 0, max: 300, step: 1 },
  { group: "classifier", key: "rule_frames", label: K("engineSettings.followUpPhotosToCheck"), hint: K("engineSettings.warningSignQuestionsLookAt"), min: 1, max: 6, step: 1 },
  { group: "classifier", key: "rule_max_queue", label: K("engineSettings.skipQuestionsWhenThisMany"), hint: K("engineSettings.whileMoreEventsThanThis"), min: 0, max: 200, step: 1 },
  { group: "calibration", key: "speed_limit_kmh", label: K("engineSettings.speedLimitKmH"), hint: K("engineSettings.anythingFasterGetsTheWarning"), min: 5, max: 300, step: 5 },
];

const showValue = (t: T, v: unknown) => (v === undefined || v === null ? t("engineSettings.automatic") : Array.isArray(v) ? t("common.points", { n: v.length }) : String(v));
const FROM_KEY = { camera: "engineSettings.fromCamera", defaults: "engineSettings.fromDefaults", builtin: "engineSettings.fromBuiltin" } as const;

/** One row: shows what is inherited, lets the admin override, and reset back to inheriting. */
function Row({ label, hint, from, inheritedText, overridden, onReset, children }: { label: string; hint?: string; from: keyof typeof FROM_KEY | "unset"; inheritedText: string; overridden: boolean; onReset: () => void; children: React.ReactNode }) {
  const t = useT();
  return (
    <div className="grid grid-cols-[1fr_190px] items-start gap-4 border-b border-border py-3 last:border-0">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="text-xs text-text-2">{hint}</p>}
        <p className="mt-1 font-mono text-[11px] text-text-3">{overridden ? <>{t("engineSettings.otherwise")} <span className="text-text-2">{inheritedText}</span></> : <>{t("engineSettings.uses")} <span className="text-text-2">{inheritedText}</span>{from !== "unset" && <> · {t("engineSettings.from")} {t(FROM_KEY[from])}</>}</>}</p>
      </div>
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">{children}</div>
        <Button type="button" variant="ghost" size="icon" aria-label={t("engineSettings.resetName", { label })} disabled={!overridden} onClick={onReset} className="size-8"><RotateCcw className="size-3.5" aria-hidden /></Button>
      </div>
    </div>
  );
}

/** The editable form for one scope: a camera (overrides over the defaults) or the defaults themselves (over the built-in values). */
export function EngineConfigForm({ scope, draft, setDraft, defaults, source, onEditGeometry }: {
  scope: "camera" | "defaults"; draft: EngineConfig; setDraft: (c: EngineConfig) => void; defaults: EngineConfig; source?: SourceRow; onEditGeometry?: () => void;
}) {
  const t = useT();
  const above = scope === "camera" ? defaults : undefined; // what this scope inherits from
  const put = (group: Group, key: string, v: unknown) => setDraft({ ...draft, [group]: { ...(draft[group] as object | undefined), [key]: v } } as EngineConfig);
  const cur = (group: Group, key: string) => (draft[group] as Record<string, unknown> | undefined)?.[key];
  const inh = (group: Group, key: string) => effective(group, key, above, undefined);
  const cal = { ...(draft.calibration ?? {}) };
  const method = cal.method ?? (inh("calibration", "method").value as string);

  return (
    <div className="space-y-5">
      {scope === "defaults" && (
        <section>
          <h3 className="caps border-b border-border pb-2">{t("engineSettings.autoLanguage")}</h3>
          <Row label={t("engineSettings.autoLanguage")} hint={t("engineSettings.autoLanguageNote")} from="unset" inheritedText={t("engineSettings.autoLanguageId")} overridden={draft.language != null} onReset={() => setDraft({ ...draft, language: null })}>
            <select aria-label={t("engineSettings.autoLanguage")} value={draft.language ?? "id"} onChange={(e) => setDraft({ ...draft, language: e.target.value as AutoLanguage })} className="h-8 w-full rounded-sm border border-border-strong bg-background px-2 text-sm">
              <option value="id">{t("engineSettings.autoLanguageId")}</option><option value="en">{t("engineSettings.autoLanguageEn")}</option>
            </select>
          </Row>
        </section>
      )}
      {(["detector", "tracker", "events"] as const).map((g) => (
        <section key={g}>
          <h3 className="caps border-b border-border pb-2">{g === "detector" ? t("engineSettings.cameraAi") : g === "tracker" ? t("engineSettings.followingObjects") : t("engineSettings.events")}</h3>
          {NUM_FIELDS.filter((f) => f.group === g).map((f) => {
            const i = inh(f.group, f.key), v = cur(f.group, f.key);
            return (
              <Row key={f.key} label={t(f.label)} hint={t(f.hint)} from={i.from} inheritedText={showValue(t, i.value)} overridden={v != null} onReset={() => put(f.group, f.key, null)}>
                <Input aria-label={t(f.label)} type="number" min={f.min} max={f.max} step={f.step} value={(v as number | null | undefined) ?? ""} placeholder={showValue(t, i.value)}
                  onChange={(e) => put(f.group, f.key, e.target.value === "" ? null : Number(e.target.value))} className="h-8 border-border-strong bg-background font-mono" />
              </Row>
            );
          })}
        </section>
      ))}

      <section>
        <h3 className="caps border-b border-border pb-2">{t("engineSettings.speedSetup")}</h3>
        {NUM_FIELDS.filter((f) => f.group === "calibration").map((f) => {
          const i = inh(f.group, f.key), v = cur(f.group, f.key);
          return (
            <Row key={f.key} label={t(f.label)} hint={t(f.hint)} from={i.from} inheritedText={showValue(t, i.value)} overridden={v != null} onReset={() => put(f.group, f.key, null)}>
              <Input aria-label={t(f.label)} type="number" min={f.min} max={f.max} step={f.step} value={(v as number | null | undefined) ?? ""} placeholder={showValue(t, i.value)}
                onChange={(e) => put(f.group, f.key, e.target.value === "" ? null : Number(e.target.value))} className="h-8 border-border-strong bg-background font-mono" />
            </Row>
          );
        })}
        {(() => { const i = inh("calibration", "speed_severity"), v = cur("calibration", "speed_severity"); return (
          <Row label={t("engineSettings.howSeriousAnOverLimit")} hint={t("engineSettings.hintOverSpeed")} from={i.from} inheritedText={showValue(t, i.value)} overridden={v != null} onReset={() => put("calibration", "speed_severity", null)}>
            <select aria-label={t("engineSettings.howSeriousAnOverLimit")} value={(v as string | null | undefined) ?? ""} onChange={(e) => put("calibration", "speed_severity", e.target.value || null)} className="h-8 w-full rounded-sm border border-border-strong bg-background px-2 text-sm">
              <option value="">{t("engineSettings.useTheSharedValue")}</option>{SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Row>); })()}
        {(() => { const i = inh("calibration", "show_geometry"), v = cur("calibration", "show_geometry"); return (
          <Row label={t("engineSettings.showTheLinesOnThe")} hint={t("engineSettings.hintShowLines")} from={i.from} inheritedText={showValue(t, i.value)} overridden={v != null} onReset={() => put("calibration", "show_geometry", null)}>
            <Switch aria-label={t("engineSettings.showTheLinesOnThe")} checked={(v as boolean | undefined) ?? (i.value as boolean | undefined) ?? true} onCheckedChange={(c) => put("calibration", "show_geometry", c)} />
          </Row>); })()}
        {scope === "camera" && source && (
          <div className="flex items-center justify-between gap-4 py-3">
            <div><p className="text-sm font-medium">{t("engineSettings.speedMeasurement")}</p>
              <p className="text-xs text-text-2">{method === "line" ? t("engineSettings.twoLinesSummary", { dir: cal.line_axis === "y" ? t("engineSettings.dirUpDown") : t("engineSettings.dirLeftRight"), m: cal.distance_m ?? "?" }) : method === "homography" ? t("engineSettings.angledViewFourCorners") : t("engineSettings.offNoSpeedsAreEstimated")}</p></div>
            <Button type="button" variant="outline" onClick={onEditGeometry}>{t("engineSettings.setUpOnACamera")}</Button>
          </div>
        )}
        {scope === "defaults" && <p className="py-3 text-xs text-text-2">{t("engineSettings.theLinesAndCornersBelong")}</p>}
      </section>

      {scope === "defaults" && (
        <section>
          <h3 className="caps border-b border-border pb-2">{t("engineSettings.secondAiCheck")} <StatusChip className="ml-2 align-middle">{t("engineSettings.allCameras")}</StatusChip></h3>
          <p className="py-2 text-xs text-text-2">{t("engineSettings.oneSecondAiCheckServes")}</p>
          {(() => { const i = inh("classifier", "rules_enabled"), v = cur("classifier", "rules_enabled"); return (
            <Row label={t("engineSettings.warningSignQuestions")} hint={t("engineSettings.hintQuestionsOff")} from={i.from} inheritedText={showValue(t, i.value)} overridden={v != null} onReset={() => put("classifier", "rules_enabled", null)}>
              <Switch aria-label={t("engineSettings.warningSignQuestions")} checked={(v as boolean | undefined) ?? (i.value as boolean | undefined) ?? true} onCheckedChange={(c) => put("classifier", "rules_enabled", c)} />
            </Row>); })()}
          {NUM_FIELDS.filter((f) => f.group === "classifier").map((f) => {
            const i = inh(f.group, f.key), v = cur(f.group, f.key);
            return (
              <Row key={f.key} label={t(f.label)} hint={t(f.hint)} from={i.from} inheritedText={showValue(t, i.value)} overridden={v != null} onReset={() => put(f.group, f.key, null)}>
                <Input aria-label={t(f.label)} type="number" min={f.min} max={f.max} step={f.step} value={(v as number | null | undefined) ?? ""} placeholder={showValue(t, i.value)}
                  onChange={(e) => put(f.group, f.key, e.target.value === "" ? null : Number(e.target.value))} className="h-8 border-border-strong bg-background font-mono" />
              </Row>
            );
          })}
          {(() => { const i = inh("classifier", "batch"), v = cur("classifier", "batch"); return (
            <Row label={t("engineSettings.askAllQuestionsAtOnce")} hint={t("engineSettings.hintBatch")} from={i.from} inheritedText={showValue(t, i.value)} overridden={v != null} onReset={() => put("classifier", "batch", null)}>
              <Switch aria-label={t("engineSettings.askAllQuestionsAtOnce")} checked={(v as boolean | undefined) ?? (i.value as boolean | undefined) ?? false} onCheckedChange={(c) => put("classifier", "batch", c)} />
            </Row>); })()}
        </section>
      )}
    </div>
  );
}

const lines = (c: EngineConfig) => Object.values(compact(c)).reduce<number>((n, g) => n + (typeof g === "object" && g !== null ? Object.keys(g).length : 1), 0);

/** Per-camera dialog. Saving stores only real overrides; the worker applies them within a poll (about 5 s). */
export function EngineSettingsDialog({ open, onOpenChange, source, defaults, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; source: SourceRow; defaults: EngineConfig; onSaved: () => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState<EngineConfig>(() => compact(source.config));
  const [geom, setGeom] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problems = useMemo(() => {
    const parsed = parseConfig(draft, "camera", t);
    return parsed.ok ? checkEffective(mergeConfig(defaults, parsed.config), t) : parsed.errors;
  }, [draft, defaults, t]);

  async function save() {
    setBusy(true); setErr(null);
    try {
      await api(`/api/sources/${source.id}/config`, { method: "PUT", json: compact(draft) });
      toast.success(t("engineSettings.cameraSaved", { code: source.code }), { description: t("engineSettings.appliedWithinAbout5Seconds") });
      onSaved(); onOpenChange(false);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <>
      <Dialog open={open && !geom} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[92vh] sm:max-w-2xl overflow-y-auto rounded-lg border-border bg-surface-2">
          <DialogHeader>
            <DialogTitle>{t("engineSettings.cameraAiSettings")} · {source.code}</DialogTitle>
            <DialogDescription>{t("engineSettings.eachFieldUsesTheSettings")}</DialogDescription>
          </DialogHeader>
          <EngineConfigForm scope="camera" draft={draft} setDraft={setDraft} defaults={defaults} source={source} onEditGeometry={() => setGeom(true)} />
          {(err || problems.length > 0) && <ul role="alert" className="space-y-1 rounded-sm border border-sev-critical/40 bg-sev-critical/10 p-3 text-sm text-sev-critical">{[...(err ? [err] : []), ...problems].map((p) => <li key={p}>{p}</li>)}</ul>}
          <DialogFooter className="items-center sm:justify-between">
            <span className="font-mono text-xs text-text-3">{lines(draft)} {t("engineSettings.setting")}{lines(draft) === 1 ? "" : "s"} {t("engineSettings.changedForThisCamera")}</span>
            <span className="flex gap-2"><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("engineSettings.cancel")}</Button><Button type="button" disabled={busy || problems.length > 0} onClick={save}>{t("engineSettings.saveSettings")}</Button></span>
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
  const t = useT();
  const [draft, setDraft] = useState<EngineConfig>(() => compact(defaults));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problems = useMemo(() => { const p = parseConfig(draft, "defaults", t); return p.ok ? checkEffective(p.config, t) : p.errors; }, [draft, t]);
  async function save() {
    setBusy(true); setErr(null);
    try {
      await api("/api/settings/engine", { method: "PUT", json: compact(draft) });
      toast.success(t("engineSettings.settingsForAllCamerasSaved"), { description: t("engineSettings.camerasWithoutTheirOwnValue") });
      onSaved(); onOpenChange(false);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] sm:max-w-2xl overflow-y-auto rounded-lg border-border bg-surface-2">
        <DialogHeader>
          <DialogTitle>{t("engineSettings.settingsForAllCameras")} <StatusChip className="ml-2 align-middle">{t("engineSettings.shared")}</StatusChip></DialogTitle>
          <DialogDescription>{t("engineSettings.usedByEveryCameraThat")}</DialogDescription>
        </DialogHeader>
        <EngineConfigForm scope="defaults" draft={draft} setDraft={setDraft} defaults={{}} />
        {(err || problems.length > 0) && <ul role="alert" className="space-y-1 rounded-sm border border-sev-critical/40 bg-sev-critical/10 p-3 text-sm text-sev-critical">{[...(err ? [err] : []), ...problems].map((p) => <li key={p}>{p}</li>)}</ul>}
        <DialogFooter><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("engineSettings.cancel")}</Button><Button type="button" disabled={busy || problems.length > 0} onClick={save}>{t("engineSettings.saveForAllCameras")}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Compact summary shown on the Sources page: what this camera overrides. */
export function overrideSummary(c: EngineConfig | undefined, t: T): string[] {
  const out: string[] = [];
  for (const [g, leaves] of Object.entries(compact(c))) if (g === "language") out.push(`${fieldLabel(t, [g])}: ${String(leaves)}`); else for (const [k, v] of Object.entries(leaves as object)) out.push(`${fieldLabel(t, [g, k])}: ${showValue(t, v)}`);
  return out;
}
