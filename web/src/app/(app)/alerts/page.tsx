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
import { dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AutomationRow, ConnectionRow, RunListRow } from "@/lib/server/automations";
import type { BehaviorRule, CategoryRow, SourceRow } from "@/lib/types";

const SENT_AS: Record<string, string> = { webhook: "Web service", mqtt: "Message", modbus: "PLC" };
const clip = (s: string, n = 90) => (s.length > n ? `${s.slice(0, n)}…` : s);

/** One line saying what was (or would have been) sent. */
function summaryOf(r: RunListRow): string {
  const d = r.detail as { reason?: string; actions?: { type: string; would_send?: unknown; message?: string }[] };
  const acts = (d.actions ?? []).map((a) => `${SENT_AS[a.type] ?? a.type}: ${clip(a.would_send == null ? (a.message ?? "") : typeof a.would_send === "string" ? a.would_send : JSON.stringify(a.would_send))}`);
  return acts.length ? acts.join(" · ") : (d.reason ?? "—");
}

export default function AlertsPage() {
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
      <PageHeader title="Alerts" badge={live > 0 ? <StatusChip tone="danger" dot className="h-6">{live} LIVE</StatusChip> : <StatusChip className="h-6">PRACTICE ONLY</StatusChip>}
        subtitle={`${automations.length} alert${automations.length === 1 ? "" : "s"} · ${connections.length} place${connections.length === 1 ? "" : "s"} to send to · alerts and indications only, every one is logged`}
        actions={admin && <Button onClick={() => setEditing("new")}>New alert</Button>} />
      <div className="px-8 pb-10">
        <Tabs defaultValue="alerts">
          <TabsList>
            <TabsTrigger value="alerts">Alerts</TabsTrigger>
            <TabsTrigger value="connections">Where alerts go</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
          </TabsList>

          <TabsContent value="alerts" className="mt-4 space-y-3">
            {automations.length === 0 && <Panel><EmptyState title="No alerts yet" action={admin ? <Button onClick={() => setEditing("new")}>New alert</Button> : undefined}>An alert says “when this happens, send that”. New alerts start in practice mode: nothing is sent until you go live.</EmptyState></Panel>}
            {automations.map((a) => (
              <Panel key={a.id} bodyClassName="space-y-3 p-5">
                <div className="flex flex-wrap items-center gap-3">
                  <h2 className="text-base font-semibold">{a.name}</h2>
                  {a.mode === "live" ? <StatusChip tone="danger" dot title="Really sends messages and switches outputs">LIVE</StatusChip> : <StatusChip tone="accent" title="Practice: shows what would be sent, sends nothing">PRACTICE</StatusChip>}
                  {!a.enabled && <StatusChip>SWITCHED OFF</StatusChip>}
                  <div className="ml-auto flex items-center gap-2">
                    {admin ? (
                      <>
                        <div role="group" aria-label={`Mode of ${a.name}`} className="flex overflow-hidden rounded-sm border border-border-strong text-xs">
                          <button type="button" aria-pressed={a.mode === "simulate"} onClick={() => a.mode !== "simulate" && patch(a, { mode: "simulate" })} className={cn("px-3 py-1.5", a.mode === "simulate" ? "bg-surface-2 font-semibold" : "text-text-2")}>Practice</button>
                          <button type="button" aria-pressed={a.mode === "live"} onClick={() => a.mode !== "live" && setGoingLive(a)} className={cn("border-l border-border-strong px-3 py-1.5", a.mode === "live" ? "bg-sev-critical/20 font-semibold text-sev-critical" : "text-text-2")}>Live</button>
                        </div>
                        <Switch aria-label={`Switch ${a.name} on`} checked={a.enabled} onCheckedChange={(v) => patch(a, { enabled: v })} />
                        <Button size="sm" variant="outline" onClick={() => queueAndWait(`/api/automations/${a.id}/preview`, `Preview of ${a.name}`, setResult)}>Preview</Button>
                        <Button size="sm" variant="outline" onClick={() => setSendingTest(a)}>Send test</Button>
                        <Button size="icon" variant="ghost" aria-label={`Edit ${a.name}`} onClick={() => setEditing(a)}><Pencil className="size-4" aria-hidden /></Button>
                        <Button size="icon" variant="ghost" aria-label={`Delete ${a.name}`} className="text-sev-critical" onClick={() => setRemoving(a)}><Trash2 className="size-4" aria-hidden /></Button>
                      </>
                    ) : <StatusChip tone={a.enabled ? "ok" : "neutral"} dot>{a.enabled ? "ON" : "OFF"}</StatusChip>}
                  </div>
                </div>
                <p className="text-sm leading-relaxed text-text-2">{describeAutomation(a.conditions, a.actions, cameras, connections, rules)}</p>
                {a.conditions.length === 0 && <p className="text-xs text-sev-high">This fires for every event.</p>}
                <p className="font-mono text-[11px] text-text-3">Quiet period {a.cooldown_s} s per camera · at most {a.max_per_hour} per hour</p>
              </Panel>
            ))}
          </TabsContent>

          <TabsContent value="connections" className="mt-4 space-y-3">
            <div className="flex items-center justify-between"><p className="text-sm text-text-2">The places alerts can be sent to. Passwords and secrets are never shown.</p>{admin && <Button variant="outline" onClick={() => setEditingConn("new")}>Add a connection</Button>}</div>
            {connections.length === 0 && <Panel><EmptyState title="Nowhere to send alerts yet">Add a web service, a message broker or a PLC, then use it in an alert.</EmptyState></Panel>}
            <div className="grid gap-3 lg:grid-cols-2">
              {connections.map((c) => {
                const cfg = c.config as Record<string, unknown>;
                return (
                  <Panel key={c.id} bodyClassName="space-y-3 p-5">
                    <div className="flex items-center gap-3"><h2 className="text-base font-semibold">{c.name}</h2><StatusChip>{KIND_TEXT[c.kind].name}</StatusChip>{!c.enabled && <StatusChip>SWITCHED OFF</StatusChip>}</div>
                    <p className="break-all font-mono text-xs text-text-2">{c.kind === "webhook" ? String(cfg.url ?? "") : `${cfg.host ?? ""}:${cfg.port ?? ""}`}</p>
                    <p className="font-mono text-[11px] text-text-3">{c.kind === "webhook" ? `${cfg.method ?? "POST"} · signing secret ${cfg.secret ? "set" : "not set"}` : c.kind === "mqtt" ? `password ${cfg.password ? "set" : "not set"}` : `unit ${cfg.unit_id ?? 1}`} · gives up after {String(cfg.timeout_s ?? 5)} s</p>
                    {admin && <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => queueAndWait(`/api/connections/${c.id}/test`, `Check connection · ${c.name}`, setResult)}>Check connection</Button><Button size="sm" variant="ghost" onClick={() => setEditingConn(c)}>Edit</Button><Button size="sm" variant="ghost" className="text-sev-critical" onClick={() => setRemovingConn(c)}>Delete</Button></div>}
                  </Panel>
                );
              })}
            </div>
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <Panel title="Latest runs" aside="Everyone can read this log">
              {!runs.data ? <LoadingList rows={4} /> : runs.data.length === 0 ? <EmptyState title="Nothing has run yet">Practice runs, tests and real alerts all show up here.</EmptyState> : (
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-border text-left"><th className="w-8 py-3 pl-4" /><th className="caps py-3 font-normal">When</th><th className="caps py-3 font-normal">Alert</th><th className="caps py-3 font-normal">Camera</th><th className="caps py-3 font-normal">Result</th><th className="caps py-3 pr-5 font-normal">What was sent</th></tr></thead>
                  <tbody>
                    {runs.data.map((r) => (
                      <Fragment key={r.id}>
                        <tr onClick={() => setOpen(open === r.id ? null : r.id)} aria-expanded={open === r.id} className="cursor-pointer border-b border-border align-top hover:bg-surface-2">
                          <td className="py-3 pl-4 text-text-3">{open === r.id ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}</td>
                          <td className="py-3 font-mono text-xs">{dateTime(r.at)}</td>
                          <td className="py-3">{r.automation_name ?? (r.connection_name ? `Connection check · ${r.connection_name}` : "—")}{r.mode.startsWith("test") && <span className="ml-2 font-mono text-[11px] text-text-3">{r.mode === "test_send" ? "TEST" : "PREVIEW"}</span>}</td>
                          <td className="py-3 font-mono text-xs">{r.source_code ?? "—"}</td>
                          <td className="py-3"><RunStatusChip status={r.status} /></td>
                          <td className="py-3 pr-5 text-text-2">{summaryOf(r)}</td>
                        </tr>
                        {open === r.id && <tr key={`${r.id}-d`} className="border-b border-border bg-surface-2"><td /><td colSpan={5} className="space-y-2 py-4 pr-5"><p className="font-mono text-[11px] text-text-3">{r.event_code ? `Event ${r.event_code}` : "No event (test)"} · {r.finished_at ? `finished ${dateTime(r.finished_at)}` : "not finished"}</p><ActionDetail detail={r.detail as never} /></td></tr>}
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
      <ConfirmDialog open={!!goingLive} onOpenChange={(o) => !o && setGoingLive(null)} title={`Switch “${goingLive?.name}” to live?`} confirmLabel="Go live" variant="destructive"
        description={<span className="space-y-2 block"><span className="block">Live alerts really send messages and switch outputs when their conditions match.</span><span className="block font-medium text-foreground">Use them for alerts and indications only, such as a lamp, a siren relay or a message. Never connect them to an emergency stop, an interlock, a door release or machine control: the camera AI can be wrong or late.</span><span className="block">Going live is recorded in the audit log under your name.</span></span>}
        onConfirm={async () => { await patch(goingLive!, { mode: "live", confirm_live: true }); toast.success(`${goingLive!.name} is live`); }} />
      <ConfirmDialog open={!!sendingTest} onOpenChange={(o) => !o && setSendingTest(null)} title={`Send a test for “${sendingTest?.name}”?`} confirmLabel="Send test"
        description="This really sends a sample message (marked as a test) to every place this alert uses, even in practice mode. Use Preview to see it without sending."
        onConfirm={() => { void queueAndWait(`/api/automations/${sendingTest!.id}/test`, `Test of ${sendingTest!.name}`, setResult); }} />
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Delete “${removing?.name}”?`} confirmLabel="Delete alert" variant="destructive"
        description="It stops sending. Its past runs are deleted with it." onConfirm={async () => { await api(`/api/automations/${removing!.id}`, { method: "DELETE" }); toast.success("Alert deleted"); await refreshAll(); }} />
      <ConfirmDialog open={!!removingConn} onOpenChange={(o) => !o && setRemovingConn(null)} title={`Delete “${removingConn?.name}”?`} confirmLabel="Delete connection" variant="destructive"
        description="You cannot delete a connection that an alert still uses." onConfirm={async () => { try { await api(`/api/connections/${removingConn!.id}`, { method: "DELETE" }); toast.success("Connection deleted"); await refreshAll(); } catch (e) { toast.error((e as Error).message); } }} />
      <TestResultDialog state={result} onClose={() => setResult(null)} />
    </>
  );
}
