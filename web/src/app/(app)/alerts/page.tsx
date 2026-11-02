"use client";
import { Fragment, useState } from "react";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader, Panel } from "@/components/console/page-header";
import { StatusChip } from "@/components/console/status-chip";
import { ConfirmDialog } from "@/components/console/confirm-dialog";
import { EmptyState, ErrorState, LoadingList } from "@/components/console/states";
import { AutomationDialog } from "@/components/console/automation-builder";
import { ConnectionDialog, KIND_TEXT } from "@/components/console/connection-dialog";
import { ActionDetail, RunStatusChip, TestResultDialog, queueAndWait, type TestState } from "@/components/console/run-result";
import { api, revalidate, useApi } from "@/hooks/use-api";
import { describeAutomation } from "@/lib/automations";
import { cn } from "@/lib/utils";
import type { AutomationRow, ConnectionRow, RunListRow } from "@/lib/server/automations";
import type { BehaviorRule, CategoryRow, SourceRow } from "@/lib/types";
import { tOr, type T } from "@/lib/i18n";
import { useFmt, useT } from "@/lib/i18n/client";

const clip = (s: string, n = 90) => (s.length > n ? `${s.slice(0, n)}…` : s);

/** One line saying what was (or would have been) sent. */
function summaryOf(r: RunListRow, t: T): string {
  const d = r.detail as { reason?: string; actions?: { type: string; would_send?: unknown; message?: string }[] };
  const acts = (d.actions ?? []).map((a) => `${tOr(t, `alerts.sentAs.${a.type}`, a.type)}: ${clip(a.would_send == null ? (a.message ?? "") : typeof a.would_send === "string" ? a.would_send : JSON.stringify(a.would_send))}`);
  return acts.length ? acts.join(" · ") : (d.reason ?? "—");
}

export default function AlertsPage() {
  const t = useT();
  const fmt = useFmt();
  const { data: me } = useApi<{ role: string }>("/api/me", 60000);
  const admin = me?.role === "admin";
  const autos = useApi<AutomationRow[]>("/api/automations", 5000);
  const conns = useApi<ConnectionRow[]>("/api/connections", 10000);
  const runs = useApi<RunListRow[]>("/api/automation-runs?limit=100", 3000);
  const { data: cams } = useApi<SourceRow[]>("/api/sources", 30000);
  const { data: cat } = useApi<{ categories: CategoryRow[]; rules: BehaviorRule[] }>("/api/categories", 60000);

  const [editing, setEditing] = useState<AutomationRow | "new" | null>(null);
  const [editingConn, setEditingConn] = useState<ConnectionRow | "new" | null>(null);
  const [removing, setRemoving] = useState<AutomationRow | null>(null);
  const [removingConn, setRemovingConn] = useState<ConnectionRow | null>(null);
  const [goingLive, setGoingLive] = useState<AutomationRow | null>(null);
  const [sendingTest, setSendingTest] = useState<AutomationRow | null>(null);
  const [result, setResult] = useState<TestState | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  if (autos.loading || conns.loading) return <LoadingList />;
  if (!autos.data || !conns.data) return <ErrorState onRetry={() => { autos.refresh(); conns.refresh(); }} message={(autos.error ?? conns.error)?.message} />;
  const automations = autos.data, connections = conns.data, cameras = cams ?? [], rules = cat?.rules ?? [];
  const live = automations.filter((a) => a.enabled && a.mode === "live").length;
  const refreshAll = () => Promise.all([revalidate("/api/automations"), revalidate("/api/connections"), revalidate("/api/automation-runs")]);
  const patch = async (a: AutomationRow, json: object) => { try { await api(`/api/automations/${a.id}`, { method: "PUT", json }); await refreshAll(); } catch (e) { toast.error((e as Error).message); } };

  return (
    <>
      <PageHeader title={t("alerts.alerts")} badge={live > 0 ? <StatusChip tone="danger" dot className="h-6">{live} {t("alerts.live")}</StatusChip> : <StatusChip className="h-6">{t("alerts.practiceOnly")}</StatusChip>}
        subtitle={t("alerts.subtitle", { alerts: automations.length, places: connections.length })}
        actions={admin && <Button onClick={() => setEditing("new")}>{t("alerts.newAlert")}</Button>} />
      <div className="px-8 pb-10">
        <Tabs defaultValue="alerts">
          <TabsList>
            <TabsTrigger value="alerts">{t("alerts.alerts")}</TabsTrigger>
            <TabsTrigger value="connections">{t("alerts.whereAlertsGo")}</TabsTrigger>
            <TabsTrigger value="activity">{t("alerts.activity")}</TabsTrigger>
          </TabsList>

          <TabsContent value="alerts" className="mt-4 space-y-3">
            {automations.length === 0 && <Panel><EmptyState title={t("alerts.noAlertsYet")} action={admin ? <Button onClick={() => setEditing("new")}>{t("alerts.newAlert")}</Button> : undefined}>{t("alerts.anAlertSaysWhenThis")}</EmptyState></Panel>}
            {automations.map((a) => (
              <Panel key={a.id} bodyClassName="space-y-3 p-5">
                <div className="flex flex-wrap items-center gap-3">
                  <h2 className="text-base font-semibold">{a.name}</h2>
                  {a.mode === "live" ? <StatusChip tone="danger" dot title={t("alerts.reallySendsMessagesAndSwitches")}>{t("alerts.live")}</StatusChip> : <StatusChip tone="accent" title={t("alerts.practiceShowsWhatWouldBe")}>{t("alerts.practice")}</StatusChip>}
                  {!a.enabled && <StatusChip>{t("alerts.switchedOff")}</StatusChip>}
                  <div className="ml-auto flex items-center gap-2">
                    {admin ? (
                      <>
                        <div role="group" aria-label={t("alerts.modeOf", { name: a.name })} className="flex overflow-hidden rounded-sm border border-border-strong text-xs">
                          <button type="button" aria-pressed={a.mode === "simulate"} onClick={() => a.mode !== "simulate" && patch(a, { mode: "simulate" })} className={cn("px-3 py-1.5", a.mode === "simulate" ? "bg-surface-2 font-semibold" : "text-text-2")}>{t("alerts.practice2")}</button>
                          <button type="button" aria-pressed={a.mode === "live"} onClick={() => a.mode !== "live" && setGoingLive(a)} className={cn("border-l border-border-strong px-3 py-1.5", a.mode === "live" ? "bg-sev-critical/20 font-semibold text-sev-critical" : "text-text-2")}>{t("alerts.live2")}</button>
                        </div>
                        <Switch aria-label={t("alerts.switchOnName", { name: a.name })} checked={a.enabled} onCheckedChange={(v) => patch(a, { enabled: v })} />
                        <Button size="sm" variant="outline" onClick={() => queueAndWait(`/api/automations/${a.id}/preview`, t("alerts.previewOf", { name: a.name }), setResult)}>{t("alerts.preview")}</Button>
                        <Button size="sm" variant="outline" onClick={() => setSendingTest(a)}>{t("alerts.sendTest")}</Button>
                        <Button size="icon" variant="ghost" aria-label={t("alerts.editName", { name: a.name })} onClick={() => setEditing(a)}><Pencil className="size-4" aria-hidden /></Button>
                        <Button size="icon" variant="ghost" aria-label={t("alerts.deleteName", { name: a.name })} className="text-sev-critical" onClick={() => setRemoving(a)}><Trash2 className="size-4" aria-hidden /></Button>
                      </>
                    ) : <StatusChip tone={a.enabled ? "ok" : "neutral"} dot>{a.enabled ? t("alerts.on") : t("alerts.off")}</StatusChip>}
                  </div>
                </div>
                <p className="text-sm leading-relaxed text-text-2">{describeAutomation(a.conditions, a.actions, cameras, connections, rules, t)}</p>
                {a.conditions.length === 0 && <p className="text-xs text-sev-high">{t("alerts.thisFiresForEveryEvent")}</p>}
                <p className="font-mono text-[11px] text-text-3">{t("alerts.quietPeriod")} {a.cooldown_s} {t("alerts.sPerCameraAtMost")} {a.max_per_hour} {t("alerts.perHour")}</p>
              </Panel>
            ))}
          </TabsContent>

          <TabsContent value="connections" className="mt-4 space-y-3">
            <div className="flex items-center justify-between"><p className="text-sm text-text-2">{t("alerts.thePlacesAlertsCanBe")}</p>{admin && <Button variant="outline" onClick={() => setEditingConn("new")}>{t("alerts.addAConnection")}</Button>}</div>
            {connections.length === 0 && <Panel><EmptyState title={t("alerts.nowhereToSendAlertsYet")}>{t("alerts.addAWebServiceA")}</EmptyState></Panel>}
            <div className="grid gap-3 lg:grid-cols-2">
              {connections.map((c) => {
                const cfg = c.config as Record<string, unknown>;
                return (
                  <Panel key={c.id} bodyClassName="space-y-3 p-5">
                    <div className="flex items-center gap-3"><h2 className="text-base font-semibold">{c.name}</h2><StatusChip>{KIND_TEXT[c.kind].name}</StatusChip>{!c.enabled && <StatusChip>{t("alerts.switchedOff")}</StatusChip>}</div>
                    <p className="break-all font-mono text-xs text-text-2">{c.kind === "webhook" ? String(cfg.url ?? "") : `${cfg.host ?? ""}:${cfg.port ?? ""}`}</p>
                    <p className="font-mono text-[11px] text-text-3">{c.kind === "webhook" ? t("alerts.webhookMeta", { method: String(cfg.method ?? "POST"), secret: cfg.secret ? t("alerts.isSet") : t("alerts.notSet") }) : c.kind === "mqtt" ? t("alerts.mqttMeta", { password: cfg.password ? t("alerts.isSet") : t("alerts.notSet") }) : t("alerts.modbusMeta", { unit: String(cfg.unit_id ?? 1) })} · {t("alerts.givesUpAfter")} {String(cfg.timeout_s ?? 5)} s</p>
                    {admin && <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => queueAndWait(`/api/connections/${c.id}/test`, t("alerts.connectionCheckTitle", { name: c.name }), setResult)}>{t("alerts.checkConnection")}</Button><Button size="sm" variant="ghost" onClick={() => setEditingConn(c)}>{t("alerts.edit")}</Button><Button size="sm" variant="ghost" className="text-sev-critical" onClick={() => setRemovingConn(c)}>{t("alerts.delete")}</Button></div>}
                  </Panel>
                );
              })}
            </div>
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <Panel title={t("alerts.latestRuns")} aside={t("alerts.everyoneCanReadThisLog")}>
              {!runs.data ? <LoadingList rows={4} /> : runs.data.length === 0 ? <EmptyState title={t("alerts.nothingHasRunYet")}>{t("alerts.practiceRunsTestsAndReal")}</EmptyState> : (
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-border text-left"><th className="w-8 py-3 pl-4" /><th className="caps py-3 font-normal">{t("alerts.when")}</th><th className="caps py-3 font-normal">{t("alerts.alert")}</th><th className="caps py-3 font-normal">{t("alerts.camera")}</th><th className="caps py-3 font-normal">{t("alerts.result")}</th><th className="caps py-3 pr-5 font-normal">{t("alerts.whatWasSent")}</th></tr></thead>
                  <tbody>
                    {runs.data.map((r) => (
                      <Fragment key={r.id}>
                        <tr onClick={() => setOpen(open === r.id ? null : r.id)} aria-expanded={open === r.id} className="cursor-pointer border-b border-border align-top hover:bg-surface-2">
                          <td className="py-3 pl-4 text-text-3">{open === r.id ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}</td>
                          <td className="py-3 font-mono text-xs">{fmt.dateTime(r.at)}</td>
                          <td className="py-3">{r.automation_name ?? (r.connection_name ? t("alerts.connectionCheckRow", { name: r.connection_name }) : "—")}{r.mode.startsWith("test") && <span className="ml-2 font-mono text-[11px] text-text-3">{r.mode === "test_send" ? t("alerts.test") : t("alerts.preview2")}</span>}</td>
                          <td className="py-3 font-mono text-xs">{r.source_code ?? "—"}</td>
                          <td className="py-3"><RunStatusChip status={r.status} /></td>
                          <td className="py-3 pr-5 text-text-2">{summaryOf(r, t)}</td>
                        </tr>
                        {open === r.id && <tr key={`${r.id}-d`} className="border-b border-border bg-surface-2"><td /><td colSpan={5} className="space-y-2 py-4 pr-5"><p className="font-mono text-[11px] text-text-3">{r.event_code ? t("alerts.eventCode", { code: r.event_code }) : t("alerts.noEventTest")} · {r.finished_at ? t("alerts.finishedAt", { when: fmt.dateTime(r.finished_at) }) : t("alerts.notFinished")}</p><ActionDetail detail={r.detail as never} /></td></tr>}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              )}
            </Panel>
          </TabsContent>
        </Tabs>
      </div>

      {editing && <AutomationDialog key={editing === "new" ? "new" : editing.id} open onOpenChange={(o) => !o && setEditing(null)} automation={editing === "new" ? undefined : editing} cameras={cameras} categories={cat?.categories ?? []} rules={rules} connections={connections} nextPosition={Math.max(0, ...automations.map((a) => a.position)) + 1} onSaved={refreshAll} />}
      {editingConn && <ConnectionDialog key={editingConn === "new" ? "new" : editingConn.id} open onOpenChange={(o) => !o && setEditingConn(null)} connection={editingConn === "new" ? undefined : editingConn} onSaved={refreshAll} />}
      <ConfirmDialog open={!!goingLive} onOpenChange={(o) => !o && setGoingLive(null)} title={t("alerts.switchToLive", { name: goingLive?.name ?? "" })} confirmLabel={t("alerts.goLive")} variant="destructive"
        description={<span className="space-y-2 block"><span className="block">{t("alerts.liveAlertsReallySendMessages")}</span><span className="block font-medium text-foreground">{t("alerts.useThemForAlertsAnd")}</span><span className="block">{t("alerts.goingLiveIsRecordedIn")}</span></span>}
        onConfirm={async () => { await patch(goingLive!, { mode: "live", confirm_live: true }); toast.success(t("alerts.isLive", { name: goingLive!.name })); }} />
      <ConfirmDialog open={!!sendingTest} onOpenChange={(o) => !o && setSendingTest(null)} title={t("alerts.sendTestFor", { name: sendingTest?.name ?? "" })} confirmLabel={t("alerts.sendTest")}
        description={t("alerts.thisReallySendsASample")}
        onConfirm={() => { void queueAndWait(`/api/automations/${sendingTest!.id}/test`, t("alerts.testOf", { name: sendingTest!.name }), setResult); }} />
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={t("alerts.deleteQ", { name: removing?.name ?? "" })} confirmLabel={t("alerts.deleteAlert")} variant="destructive"
        description={t("alerts.itStopsSendingItsPast")} onConfirm={async () => { await api(`/api/automations/${removing!.id}`, { method: "DELETE" }); toast.success(t("alerts.alertDeleted")); await refreshAll(); }} />
      <ConfirmDialog open={!!removingConn} onOpenChange={(o) => !o && setRemovingConn(null)} title={t("alerts.deleteQ", { name: removingConn?.name ?? "" })} confirmLabel={t("alerts.deleteConnection")} variant="destructive"
        description={t("alerts.youCannotDeleteAConnection")} onConfirm={async () => { try { await api(`/api/connections/${removingConn!.id}`, { method: "DELETE" }); toast.success(t("alerts.connectionDeleted")); await refreshAll(); } catch (e) { toast.error((e as Error).message); } }} />
      <TestResultDialog state={result} onClose={() => setResult(null)} />
    </>
  );
}
