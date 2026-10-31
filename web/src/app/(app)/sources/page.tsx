"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { PageHeader, Panel } from "@/components/console/page-header";
import { HealthChip, StatusChip } from "@/components/console/status-chip";
import { SourceDialog } from "@/components/console/source-form";
import { ConfirmDialog } from "@/components/console/confirm-dialog";
import { LiveStream } from "@/components/console/live-stream";
import { EmptyState, ErrorState, LoadingList } from "@/components/console/states";
import { api, revalidate, useApi } from "@/hooks/use-api";
import { ago, agoText } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CategoryRow, SourceRow } from "@/lib/types";

const maskUrl = (u: string) => u.replace(/\/\/([^:@/]+):([^@/]+)@/, "//$1:••••@"); // admin UI also masks on screen

export default function SourcesPage() {
  const { data, error, loading, refresh } = useApi<SourceRow[]>("/api/sources", 4000);
  const { data: cats } = useApi<{ categories: CategoryRow[] }>("/api/categories", 60000);
  const { data: ov } = useApi<{ worker: { last_seen: string; sources: number; model: string | null; classifier: string | null } | null }>("/api/overview", 5000);
  const [sel, setSel] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<SourceRow | null>(null);
  const [removing, setRemoving] = useState<SourceRow | null>(null);
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
      <PageHeader title="Sources & health" subtitle={`${online} of ${data.length} streams online`} actions={<Button onClick={() => setAdding(true)}>Add source</Button>} />
      <div className="px-8 pb-10 space-y-5">
        <Panel title="Detection worker" aside={<StatusChip tone={workerOk ? "ok" : "danger"} dot>{workerOk ? "HEALTHY" : "NOT RESPONDING"}</StatusChip>} bodyClassName="grid grid-cols-4 gap-6 p-5">
          <div><p className="caps">Streams running</p><p className="mt-1 font-mono text-lg">{ov?.worker?.sources ?? 0}</p></div>
          <div><p className="caps">Model</p><p className="mt-1 font-mono text-lg">{ov?.worker?.model?.split("/").pop() ?? "–"}</p></div>
          <div><p className="caps">Classifier</p><p className="mt-1 font-mono text-lg">{ov?.worker?.classifier ?? "off"}</p></div>
          <div><p className="caps">Last heartbeat</p><p className="mt-1 font-mono text-lg">{ov?.worker ? `${agoText(ov.worker.last_seen)}` : "never"}</p></div>
        </Panel>
        <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
          <Panel>
            {data.length === 0 ? <EmptyState title="No sources yet" action={<Button onClick={() => setAdding(true)}>Add source</Button>}>Register an RTSP or M3U8 URL. The worker starts listening within seconds.</EmptyState> : (
              <table className="w-full text-sm">
                <thead><tr className="border-b border-border text-left"><th className="caps px-5 py-3 font-normal">Source</th><th className="caps py-3 font-normal">Protocol</th><th className="caps py-3 font-normal">Detects</th><th className="caps py-3 font-normal">Status</th><th className="caps py-3 font-normal">Seen</th><th className="caps py-3 pr-5 text-right font-normal">On</th></tr></thead>
                <tbody>
                  {data.map((s) => (
                    <tr key={s.id} onClick={() => setSel(s.id)} aria-selected={s.id === cur?.id} className={cn("cursor-pointer border-b border-border last:border-0 hover:bg-surface-2", s.id === cur?.id && "bg-surface-2 shadow-[inset_2px_0_0_var(--accent)]")}>
                      <td className="px-5 py-3"><div className="flex items-center gap-3"><Video className="size-4 text-text-3" aria-hidden /><div><p className="font-semibold">{s.code}</p><p className="text-xs text-text-2">{s.name}</p></div></div></td>
                      <td className="font-mono text-xs">{s.kind === "rtsp" ? "RTSP" : "M3U8"}</td>
                      <td><div className="flex flex-wrap gap-1">{s.labels.map((l) => <StatusChip key={l} tone="accent" className="h-5">{l}</StatusChip>)}</div></td>
                      <td>{s.enabled ? <HealthChip status={s.status} /> : <StatusChip>DISABLED</StatusChip>}</td>
                      <td className="font-mono text-xs text-text-2">{s.status === "online" ? "now" : ago(s.last_seen)}</td>
                      <td className="pr-5 text-right" onClick={(e) => e.stopPropagation()}><Switch aria-label={`Enable ${s.code}`} checked={s.enabled} onCheckedChange={(v) => toggle(s, v)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
          {cur && (
            <Panel bodyClassName="space-y-4 p-5">
              <div className="flex items-center gap-3"><h2 className="text-xl font-semibold">{cur.code}</h2>{cur.enabled ? <HealthChip status={cur.status} /> : <StatusChip>DISABLED</StatusChip>}</div>
              <LiveStream source={cur} compact />
              <dl className="grid grid-cols-[110px_1fr] gap-y-2 text-sm">
                <dt className="font-mono text-xs text-text-3">Location</dt><dd>{cur.name}</dd>
                <dt className="font-mono text-xs text-text-3">URL</dt><dd className="break-all font-mono text-xs">{maskUrl(cur.url)}</dd>
                <dt className="font-mono text-xs text-text-3">Min confidence</dt><dd className="font-mono">{cur.min_confidence.toFixed(2)}</dd>
                <dt className="font-mono text-xs text-text-3">Throughput</dt><dd className="font-mono">{cur.status === "online" ? `${cur.fps?.toFixed(1) ?? "–"} fps · ${cur.inference_ms?.toFixed(0) ?? "–"} ms` : "–"}</dd>
              </dl>
              {cur.last_error && cur.status !== "online" && <p className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 p-3 font-mono text-xs text-sev-critical">{cur.last_error}</p>}
              <div className="flex gap-2"><Button variant="outline" onClick={() => setEditing(cur)}>Edit</Button><Button variant="ghost" className="text-sev-critical" onClick={() => setRemoving(cur)}>Remove</Button></div>
            </Panel>
          )}
        </div>
      </div>
      {adding && <SourceDialog open onOpenChange={setAdding} categories={cats?.categories ?? []} nextCode={nextCode} onSaved={() => revalidate("/api/sources")} />}
      {editing && <SourceDialog open onOpenChange={(o) => !o && setEditing(null)} categories={cats?.categories ?? []} source={editing} nextCode="" onSaved={() => revalidate("/api/sources")} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Remove ${removing?.code}?`} confirmLabel="Remove source" variant="destructive"
        description="The worker stops listening. Its events and incidents are deleted with it." onConfirm={async () => { await api(`/api/sources/${removing!.id}`, { method: "DELETE" }); toast.success("Source removed"); await revalidate("/api/"); }} />
    </>
  );
}
