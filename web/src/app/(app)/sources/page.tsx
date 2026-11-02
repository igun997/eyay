"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { PageHeader, Panel } from "@/components/console/page-header";
import { HealthChip, StatusChip } from "@/components/console/status-chip";
import { SourceDialog } from "@/components/console/source-form";
import { EngineDefaultsDialog, EngineSettingsDialog, overrideSummary } from "@/components/console/engine-settings";
import { ConfirmDialog } from "@/components/console/confirm-dialog";
import { LiveStream } from "@/components/console/live-stream";
import { EmptyState, ErrorState, LoadingList } from "@/components/console/states";
import { api, revalidate, useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import type { EngineConfig } from "@/lib/engine-config";
import type { CategoryRow, SourceRow } from "@/lib/types";
import { useFmt, useT } from "@/lib/i18n/client";

const maskUrl = (u: string) => u.replace(/\/\/([^:@/]+):([^@/]+)@/, "//$1:••••@"); // admin UI also masks on screen

export default function SourcesPage() {
  const t = useT();
  const fmt = useFmt();
  const { data, error, loading, refresh } = useApi<SourceRow[]>("/api/sources", 4000);
  const { data: cats } = useApi<{ categories: CategoryRow[] }>("/api/categories", 60000);
  const { data: ov } = useApi<{ worker: { last_seen: string; sources: number; model: string | null; classifier: string | null } | null }>("/api/overview", 5000);
  const [sel, setSel] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<SourceRow | null>(null);
  const [removing, setRemoving] = useState<SourceRow | null>(null);
  const [tuning, setTuning] = useState<SourceRow | null>(null);
  const [defaultsOpen, setDefaultsOpen] = useState(false);
  const { data: eng, refresh: refreshEng } = useApi<{ defaults: EngineConfig }>("/api/settings/engine", 30000);
  if (loading) return <LoadingList />;
  if (!data) return <ErrorState onRetry={() => refresh()} message={error?.message} />;
  const cur = data.find((s) => s.id === sel) ?? data[0];
  const online = data.filter((s) => s.enabled && s.status === "online").length;
  const workerOk = !!ov?.worker && Date.now() - new Date(ov.worker.last_seen).getTime() < 20000;
  const nextNum = Math.max(0, ...data.map((s) => Number(/^CAM-(\d+)$/.exec(s.code)?.[1] ?? 0))) + 1;
  const nextCode = `CAM-${String(nextNum).padStart(2, "0")}`;
  const toggle = async (s: SourceRow, enabled: boolean) => { try { await api(`/api/sources/${s.id}`, { method: "PATCH", json: { enabled } }); await revalidate("/api/sources"); } catch (e) { toast.error((e as Error).message); } };

  return (
    <>
      <PageHeader title={t("sources.cameraSetupHealth")} subtitle={t("sources.subtitle", { online, total: data.length })} actions={<><Button variant="outline" onClick={() => setDefaultsOpen(true)} disabled={!eng}>{t("sources.settingsForAllCameras")}</Button><Button onClick={() => setAdding(true)}>{t("sources.addSource")}</Button></>} />
      <div className="px-8 pb-10 space-y-5">
        <Panel title={t("sources.analysisService")} aside={<StatusChip tone={workerOk ? "ok" : "danger"} dot>{workerOk ? t("sources.healthy") : t("sources.notResponding")}</StatusChip>} bodyClassName="grid grid-cols-4 gap-6 p-5">
          <div><p className="caps">{t("sources.camerasWatched")}</p><p className="mt-1 font-mono text-lg">{ov?.worker?.sources ?? 0}</p></div>
          <div><p className="caps">{t("sources.cameraAi")}</p><p className="mt-1 font-mono text-lg">{ov?.worker ? "ready" : "–"}</p></div>
          <div><p className="caps">{t("sources.secondAiCheck")}</p><p className="mt-1 font-mono text-lg">{ov?.worker?.classifier ? "on" : t("sources.offOptional")}</p></div>
          <div><p className="caps">{t("sources.lastHeartbeat")}</p><p className="mt-1 font-mono text-lg">{ov?.worker ? fmt.ago(ov.worker.last_seen) : t("sources.never")}</p></div>
        </Panel>
        <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
          <Panel>
            {data.length === 0 ? <EmptyState title={t("sources.noCamerasYet")} action={<Button onClick={() => setAdding(true)}>{t("sources.addSource")}</Button>}>{t("sources.addACameraSLive")}</EmptyState> : (
              <table className="w-full text-sm">
                <thead><tr className="border-b border-border text-left"><th className="caps px-5 py-3 font-normal">{t("sources.camera")}</th><th className="caps py-3 font-normal">{t("sources.connection")}</th><th className="caps py-3 font-normal">{t("sources.detects")}</th><th className="caps py-3 font-normal">{t("sources.status")}</th><th className="caps py-3 font-normal">{t("sources.seen")}</th><th className="caps py-3 pr-5 text-right font-normal">{t("sources.on")}</th></tr></thead>
                <tbody>
                  {data.map((s) => (
                    <tr key={s.id} onClick={() => setSel(s.id)} aria-selected={s.id === cur?.id} className={cn("cursor-pointer border-b border-border last:border-0 hover:bg-surface-2", s.id === cur?.id && "bg-surface-2 shadow-[inset_2px_0_0_var(--accent)]")}>
                      <td className="px-5 py-3"><div className="flex items-center gap-3"><Video className="size-4 text-text-3" aria-hidden /><div><p className="font-semibold">{s.code}</p><p className="text-xs text-text-2">{s.name}</p></div></div></td>
                      <td className="font-mono text-xs">{s.kind === "rtsp" ? t("sources.cameraStream") : t("sources.webStream")}</td>
                      <td><div className="flex flex-wrap gap-1">{s.labels.map((l) => <StatusChip key={l} tone="accent" className="h-5">{l}</StatusChip>)}</div></td>
                      <td>{s.enabled ? <HealthChip status={s.status} /> : <StatusChip>{t("sources.disabled")}</StatusChip>}</td>
                      <td className="font-mono text-xs text-text-2">{s.status === "online" ? t("sources.now") : fmt.ago(s.last_seen)}</td>
                      <td className="pr-5 text-right" onClick={(e) => e.stopPropagation()}><Switch aria-label={t("sources.enableCode", { code: s.code })} checked={s.enabled} onCheckedChange={(v) => toggle(s, v)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
          {cur && (
            <div className="space-y-5">
            <Panel bodyClassName="space-y-4 p-5">
              <div className="flex items-center gap-3"><h2 className="text-xl font-semibold">{cur.code}</h2>{cur.enabled ? <HealthChip status={cur.status} /> : <StatusChip>{t("sources.disabled")}</StatusChip>}</div>
              <LiveStream source={cur} compact />
              <dl className="grid grid-cols-[110px_1fr] gap-y-2 text-sm">
                <dt className="font-mono text-xs text-text-3">{t("sources.location")}</dt><dd>{cur.name}</dd>
                <dt className="font-mono text-xs text-text-3">{t("sources.url")}</dt><dd className="break-all font-mono text-xs">{maskUrl(cur.url)}</dd>
                <dt className="font-mono text-xs text-text-3">{t("sources.minCertainty")}</dt><dd className="font-mono">{fmt.num(cur.min_confidence)}</dd>
                <dt className="font-mono text-xs text-text-3">{t("sources.speed")}</dt><dd className="font-mono">{cur.status === "online" ? t("sources.throughput", { fps: cur.fps != null ? fmt.num(cur.fps, 1) : "–", ms: cur.inference_ms != null ? fmt.num(cur.inference_ms, 0) : "–" }) : "–"}</dd>
              </dl>
              {cur.last_error && cur.status !== "online" && <p className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 p-3 font-mono text-xs text-sev-critical">{cur.last_error}</p>}
              <div className="flex gap-2"><Button variant="outline" onClick={() => setEditing(cur)}>{t("sources.edit")}</Button><Button variant="ghost" className="text-sev-critical" onClick={() => setRemoving(cur)}>{t("sources.remove")}</Button></div>
            </Panel>
            <Panel title={t("sources.cameraAiSettings")} aside={cur.config && overrideSummary(cur.config, t).length ? t("sources.changedN", { n: overrideSummary(cur.config, t).length }) : t("sources.usesTheSharedSettings")} bodyClassName="space-y-3 p-5">
              {overrideSummary(cur.config, t).length > 0 ? <ul className="space-y-1 font-mono text-xs text-text-2">{overrideSummary(cur.config, t).map((l) => <li key={l}>{l}</li>)}</ul>
                : <p className="text-sm text-text-2">{t("sources.thisCameraUsesTheSettings")}</p>}
              <Button variant="outline" onClick={() => setTuning(cur)} disabled={!eng}>{t("sources.cameraAiSettings")}</Button>
            </Panel>
            </div>
          )}
        </div>
      </div>
      {adding && <SourceDialog open onOpenChange={setAdding} categories={cats?.categories ?? []} nextCode={nextCode} onSaved={() => revalidate("/api/sources")} />}
      {editing && <SourceDialog open onOpenChange={(o) => !o && setEditing(null)} categories={cats?.categories ?? []} source={editing} nextCode="" onSaved={() => revalidate("/api/sources")} />}
      {tuning && eng && <EngineSettingsDialog key={tuning.id} open onOpenChange={(o) => !o && setTuning(null)} source={tuning} defaults={eng.defaults} onSaved={() => revalidate("/api/sources")} />}
      {defaultsOpen && eng && <EngineDefaultsDialog open onOpenChange={setDefaultsOpen} defaults={eng.defaults} onSaved={() => refreshEng()} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={t("sources.removeQ", { code: removing?.code ?? "" })} confirmLabel={t("sources.removeCamera")} variant="destructive"
        description={t("sources.itStopsBeingWatchedIts")} onConfirm={async () => { await api(`/api/sources/${removing!.id}`, { method: "DELETE" }); toast.success(t("sources.cameraRemoved")); await revalidate("/api/"); }} />
    </>
  );
}
