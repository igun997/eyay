"use client";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { StatusChip } from "@/components/console/status-chip";
import { api } from "@/hooks/use-api";
import { waitForRun, type RunRow } from "@/lib/automations";
import type { RunListRow } from "@/lib/server/automations";
import { useT } from "@/lib/i18n/client";
import { K, type MessageKey } from "@/lib/i18n";

type Tone = "neutral" | "accent" | "ok" | "warn" | "danger";
const STATUS: Record<string, { tone: Tone; text: MessageKey; help: MessageKey }> = {
  sent: { tone: "ok", text: K("runResult.sent"), help: K("runResult.theMessageOrOutputChange") },
  simulated: { tone: "accent", text: K("runResult.simulated"), help: K("runResult.practiceRunThisIsWhat") },
  failed: { tone: "danger", text: K("runResult.failed"), help: K("runResult.somethingWentWrongOpenThe") },
  skipped: { tone: "neutral", text: K("runResult.skipped"), help: K("runResult.notSentBecauseALimit") },
  queued: { tone: "warn", text: K("runResult.queued"), help: K("runResult.waitingForTheAnalysisService") },
  running: { tone: "accent", text: K("runResult.sending"), help: K("runResult.beingSentRightNow") },
};
export function RunStatusChip({ status }: { status: string }) {
  const t = useT();
  const s = STATUS[status];
  return <StatusChip tone={s?.tone ?? "neutral"} title={s ? t(s.help) : ""} dot={status === "queued" || status === "running"}>{s ? t(s.text) : status.toUpperCase()}</StatusChip>;
}

export type TestState =
  | { phase: "waiting"; title: string }
  | { phase: "done"; title: string; run: RunRow }
  | { phase: "timeout"; title: string }
  | { phase: "error"; title: string; message: string };

/** Queue a preview / test / connection check, then poll its row every second (up to 15 s) until it leaves `queued`. */
export async function queueAndWait(url: string, title: string, set: (s: TestState | null) => void) {
  set({ phase: "waiting", title });
  try {
    const q = await api<{ run_id: number }>(url, { method: "POST" });
    const run = await waitForRun(async () => (await api<RunListRow[]>("/api/automation-runs?limit=30")).find((r) => r.id === q.run_id) as RunRow | undefined);
    set(run ? { phase: "done", title, run } : { phase: "timeout", title });
  } catch (e) { set({ phase: "error", title, message: (e as Error).message }); }
}

const show = (v: unknown) => (v === undefined || v === null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2));

/** What was (or would have been) sent, action by action. Shared by the result dialog and the activity log. */
export function ActionDetail({ detail }: { detail: RunRow["detail"] }) {
  const t = useT();
  const actions = detail?.actions ?? [];
  return (
    <div className="space-y-3">
      {detail?.reason && <p className="text-sm text-text-2">{detail.reason}</p>}
      {actions.map((a, i) => (
        <div key={i} className="rounded-sm border border-border bg-background p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip tone={a.ok ? "ok" : "danger"}>{a.ok ? t("runResult.ok") : t("runResult.failed")}</StatusChip>
            <span className="font-medium">{a.type === "webhook" ? t("runResult.webService") : a.type === "mqtt" ? t("runResult.messageBroker") : a.type === "modbus" ? t("runResult.plc") : a.type}</span>
            {a.target && <span className="font-mono text-xs text-text-2">{a.target}</span>}
            {a.ms != null && <span className="font-mono text-xs text-text-3">{a.ms} {t("runResult.ms")}</span>}
          </div>
          {a.message && <p className="mt-2 text-text-2">{a.message}</p>}
          {a.would_send !== undefined && a.would_send !== null && <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-sm bg-surface p-2 font-mono text-xs">{show(a.would_send)}</pre>}
        </div>
      ))}
      {!actions.length && !detail?.reason && <p className="text-sm text-text-3">{t("runResult.noFurtherDetail")}</p>}
    </div>
  );
}

/** Result of a preview, a test or a connection check. */
export function TestResultDialog({ state, onClose }: { state: TestState | null; onClose: () => void }) {
  const t = useT();
  return (
    <Dialog open={!!state} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto rounded-lg border-border bg-surface-2 sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{state?.title}</DialogTitle>
          <DialogDescription>
            {state?.phase === "waiting" && t("runResult.askingTheAnalysisServiceThis")}
            {state?.phase === "done" && <>{t("runResult.result")} <RunStatusChip status={state.run.status} /></>}
            {state?.phase === "timeout" && t("runResult.theAnalysisServiceHasNot")}
            {state?.phase === "error" && state.message}
          </DialogDescription>
        </DialogHeader>
        {state?.phase === "waiting" && <p className="flex items-center gap-2 text-sm text-text-2"><Loader2 className="size-4 animate-spin" aria-hidden /> {t("runResult.waiting")}</p>}
        {state?.phase === "done" && <ActionDetail detail={state.run.detail} />}
        <DialogFooter><Button type="button" variant="outline" onClick={onClose}>{t("runResult.close")}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
