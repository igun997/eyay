"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { checkEffective, overrides, parseConfig, type EngineConfig } from "@/lib/engine-config";
import type { SourceRow } from "@/lib/types";
import { useFmt, useT } from "@/lib/i18n/client";

type Calibration = NonNullable<EngineConfig["calibration"]>;
type Pt = [number, number];

/** Starting quad (clockwise from bottom-left) and its road size: placeholders to replace with real measurements. */
const START_PIXELS: Pt[] = [[0.15, 0.9], [0.15, 0.5], [0.85, 0.5], [0.85, 0.9]];
const START_WORLD: Pt[] = [[0, 0], [0, 30], [7, 30], [7, 0]];
const CORNER_KEYS = ["corner.bl", "corner.tl", "corner.tr", "corner.br"] as const;
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const r3 = (v: number) => Math.round(v * 1000) / 1000;

type Drag = { kind: "line"; which: 1 | 2 } | { kind: "point"; index: number } | null;

/**
 * Draw the speed geometry on the camera's still. Coordinates are fractions of the frame (0..1), exactly what the worker stores,
 * so the same configuration keeps working when the camera is recorded at another resolution.
 * `inherited` is the calibration the camera would use without its own override (the defaults).
 */
export function CalibrationEditor({ open, onOpenChange, source, value, inherited, onApply }: {
  open: boolean; onOpenChange: (o: boolean) => void; source: Pick<SourceRow, "id" | "code" | "status">; value: Calibration; inherited: Calibration; onApply: (c: Calibration) => void;
}) {
  const t = useT();
  const fmt = useFmt();
  const start = (v: Calibration): Calibration => ({ ...inherited, ...v });
  const [d, setD] = useState<Calibration>(() => start(value));
  const [drag, setDrag] = useState<Drag>(null);
  const [nonce, setNonce] = useState(0);
  const [imgFailed, setImgFailed] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) { setD(start(value)); setImgFailed(false); setNonce((n) => n + 1); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const method = d.method ?? "none";
  const axis = d.line_axis ?? "x";
  const pixels = d.pixels ?? START_PIXELS;
  const world = d.world_m ?? START_WORLD;
  const set = (patch: Partial<Calibration>) => setD((c) => ({ ...c, ...patch }));

  const errors = useMemo(() => {
    const cal = { ...d, method };
    const parsed = parseConfig({ calibration: cal }, "camera", t);
    return parsed.ok ? checkEffective({ calibration: cal }, t) : parsed.errors;
  }, [d, method, t]);

  const toFrac = useCallback((e: { clientX: number; clientY: number }): Pt => {
    const r = box.current!.getBoundingClientRect();
    return [clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height)];
  }, []);

  function onMove(e: React.PointerEvent) {
    if (!drag) return;
    const [x, y] = toFrac(e);
    if (drag.kind === "line") set(drag.which === 1 ? { line1: r3(axis === "x" ? x : y) } : { line2: r3(axis === "x" ? x : y) });
    else set({ pixels: pixels.map((p, i) => (i === drag.index ? [r3(x), r3(y)] : p)) as Pt[] });
  }

  const num = (s: string) => (s.trim() === "" ? null : Number(s));
  const pickMethod = (m: NonNullable<Calibration["method"]>) => {
    if (m === "line") set({ method: m, line_axis: d.line_axis ?? "x", line1: d.line1 ?? 0.4, line2: d.line2 ?? 0.7 });
    else if (m === "homography") set({ method: m, pixels: d.pixels ?? START_PIXELS, world_m: d.world_m ?? START_WORLD });
    else set({ method: m });
  };
  const lineStyle = (v: number | null | undefined) => (axis === "x" ? { x1: v ?? 0, x2: v ?? 0, y1: 0, y2: 1 } : { y1: v ?? 0, y2: v ?? 0, x1: 0, x2: 1 });
  const handles = [d.line1, d.line2];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] sm:max-w-4xl overflow-y-auto rounded-lg border-border bg-surface-2">
        <DialogHeader>
          <DialogTitle>{t("calibrationEditor.speedSetup")} · {source.code}</DialogTitle>
          <DialogDescription>{t("calibrationEditor.dragTheLinesOrThe")}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-5 lg:grid-cols-[1fr_280px]">
          <div>
            <div ref={box} className="relative select-none overflow-hidden rounded-sm bg-black" onPointerMove={onMove} onPointerUp={() => setDrag(null)} onPointerLeave={() => setDrag(null)}>
              {!imgFailed ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={nonce} src={`/api/live/${source.id}?mode=snapshot&t=${nonce}`} alt={t("calibrationEditor.latestPictureOf", { code: source.code })} className="block w-full" draggable={false} onError={() => setImgFailed(true)} />
              ) : (
                <div className="grid aspect-video place-items-center p-6 text-center text-sm text-text-2">{t("calibrationEditor.noPictureAvailableTheCamera")}</div>
              )}
              <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="absolute inset-0 size-full touch-none" aria-label={t("calibrationEditor.speedSetupLines")}>
                {method === "line" && [d.line1, d.line2].map((v, i) => v != null && (
                  <line key={i} {...lineStyle(v)} stroke="var(--accent)" strokeWidth={0.004} vectorEffect="non-scaling-stroke" style={{ strokeWidth: 2 }} />
                ))}
                {method === "homography" && (
                  <polygon points={pixels.map((p) => p.join(",")).join(" ")} fill="var(--accent)" fillOpacity={0.12} stroke="var(--accent)" vectorEffect="non-scaling-stroke" style={{ strokeWidth: 2 }} />
                )}
              </svg>
              {method === "line" && handles.map((v, i) => v != null && (
                <button key={i} type="button" aria-label={t("calibrationEditor.lineN", { n: i + 1 })} onPointerDown={(e) => { e.preventDefault(); setDrag({ kind: "line", which: (i + 1) as 1 | 2 }); }}
                  className="absolute grid size-7 -translate-x-1/2 -translate-y-1/2 cursor-grab place-items-center rounded-full border-2 border-brand bg-background font-mono text-xs active:cursor-grabbing"
                  style={axis === "x" ? { left: `${v * 100}%`, top: "50%" } : { top: `${v * 100}%`, left: "50%" }}>{i + 1}</button>
              ))}
              {method === "homography" && pixels.map((p, i) => (
                <button key={i} type="button" aria-label={t("calibrationEditor.cornerLabel", { n: i + 1, where: t(CORNER_KEYS[i]) })} onPointerDown={(e) => { e.preventDefault(); setDrag({ kind: "point", index: i }); }}
                  className="absolute grid size-7 -translate-x-1/2 -translate-y-1/2 cursor-grab place-items-center rounded-full border-2 border-brand bg-background font-mono text-xs active:cursor-grabbing"
                  style={{ left: `${p[0] * 100}%`, top: `${p[1] * 100}%` }}>{i + 1}</button>
              ))}
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-text-2">
              <span>{method === "none" ? t("calibrationEditor.speedMeasurementIsOffFor") : method === "line" ? t("calibrationEditor.drawTheLinesStraightAcross") : t("calibrationEditor.numberTheCornersClockwiseStarting")}</span>
              <Button type="button" variant="ghost" size="sm" onClick={() => { setImgFailed(false); setNonce((n) => n + 1); }}><RefreshCw className="size-3.5" aria-hidden /> {t("calibrationEditor.refreshPicture")}</Button>
            </div>
          </div>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="cal-method">{t("calibrationEditor.howToMeasure")}</Label>
              <select id="cal-method" value={method} onChange={(e) => pickMethod(e.target.value as NonNullable<Calibration["method"]>)} className="h-9 w-full rounded-sm border border-border-strong bg-background px-2 text-sm">
                <option value="none">{t("calibrationEditor.off")}</option><option value="line">{t("calibrationEditor.twoLines")}</option><option value="homography">{t("calibrationEditor.angledViewFourCorners")}</option>
              </select>
            </div>

            {method === "line" && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="cal-axis">{t("calibrationEditor.whatItMeasures")}</Label>
                  <select id="cal-axis" value={axis} onChange={(e) => set({ line_axis: e.target.value as "x" | "y" })} className="h-9 w-full rounded-sm border border-border-strong bg-background px-2 text-sm">
                    <option value="x">{t("calibrationEditor.leftToRightMovementUpright")}</option><option value="y">{t("calibrationEditor.upAndDownMovementFlat")}</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  {([["line1", "field.line1"], ["line2", "field.line2"]] as const).map(([k, label]) => (
                    <div key={k} className="space-y-2"><Label htmlFor={`cal-${k}`}>{t(label)}</Label>
                      <Input id={`cal-${k}`} type="number" step={0.01} min={0} max={1} value={d[k] ?? ""} onChange={(e) => set({ [k]: num(e.target.value) })} className="border-border-strong bg-background font-mono" /></div>
                  ))}
                </div>
                <div className="space-y-2"><Label htmlFor="cal-dist">{t("calibrationEditor.realDistanceBetweenTheLines")}</Label>
                  <Input id="cal-dist" type="number" step={0.5} min={0.5} max={1000} value={d.distance_m ?? ""} onChange={(e) => set({ distance_m: num(e.target.value) })} placeholder={t("calibrationEditor.measureItOnSite")} className="border-border-strong bg-background font-mono" /></div>
              </>
            )}

            {method === "homography" && (
              <div className="space-y-2">
                <p className="text-sm font-medium">{t("calibrationEditor.theSameCornersInMetres")}</p>
                <table className="w-full text-sm">
                  <thead><tr className="text-left"><th className="caps py-1 font-normal">#</th><th className="caps py-1 font-normal">{t("calibrationEditor.corner")}</th><th className="caps py-1 font-normal">X (m)</th><th className="caps py-1 font-normal">Y (m)</th></tr></thead>
                  <tbody>{world.map((w, i) => (
                    <tr key={i}><td className="font-mono text-xs">{i + 1}</td><td className="text-xs text-text-2">{t(CORNER_KEYS[i])}</td>
                      {[0, 1].map((a) => (
                        <td key={a} className="pr-1"><Input aria-label={t("calibrationEditor.cornerMetres", { n: i + 1, axis: a === 0 ? "X" : "Y" })} type="number" step={0.5} value={w[a]} onChange={(e) => set({ world_m: world.map((q, j) => (j === i ? (a === 0 ? [Number(e.target.value), q[1]] : [q[0], Number(e.target.value)]) : q)) as Pt[] })} className="h-8 border-border-strong bg-background px-2 font-mono" /></td>
                      ))}</tr>
                  ))}</tbody>
                </table>
                <p className="text-xs text-text-3">{t("calibrationEditor.theStartingValuesAreOnly")}</p>
                <div className="space-y-2"><Label htmlFor="cal-smooth">{t("calibrationEditor.smoothing")} <span className="ml-2 font-mono text-xs text-text-2">{fmt.num(d.smoothing ?? 0.4)}</span></Label>
                  <input id="cal-smooth" type="range" min={0} max={1} step={0.05} value={d.smoothing ?? 0.4} onChange={(e) => set({ smoothing: Number(e.target.value) })} className="w-full accent-[var(--accent)]" /></div>
              </div>
            )}

            {method !== "none" && (
              <p role="note" className="border-l-2 border-sev-high bg-sev-high/10 p-3 text-xs text-text-2">
                <span className="font-semibold text-foreground">{t("calibrationEditor.everySpeedDependsOnThis")}</span>{" "}
                {method === "line" ? t("calibrationEditor.theDistanceScalesEverySpeed") : t("calibrationEditor.aWronglyMeasuredStretchOf")} {t("calibrationEditor.speedsAreEstimatesForA")}
              </p>
            )}
            {errors.length > 0 && (
              <ul role="alert" className="space-y-1 rounded-sm border border-sev-critical/40 bg-sev-critical/10 p-3 text-xs text-sev-critical">{errors.map((e) => <li key={e}>{e}</li>)}</ul>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("calibrationEditor.cancel")}</Button>
          <Button type="button" disabled={errors.length > 0} onClick={() => { onApply(overrides(d, value, inherited)); onOpenChange(false); }}>{t("calibrationEditor.useThisGeometry")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
