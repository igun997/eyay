"use client";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { StatusChip } from "@/components/console/status-chip";
import { api } from "@/hooks/use-api";
import { waitForRun, type RunRow } from "@/lib/automations";
import type { RunListRow } from "@/lib/server/automations";

type Tone = "neutral" | "accent" | "ok" | "warn" | "danger";
const STATUS: Record<string, { tone: Tone; text: string; help: string }> = {
  sent: { tone: "ok", text: "SENT", help: "The message or output change was really sent." },
  simulated: { tone: "accent", text: "SIMULATED", help: "Practice run: this is what would have been sent. Nothing left the building." },
  failed: { tone: "danger", text: "FAILED", help: "Something went wrong. Open the row to see what." },
  skipped: { tone: "neutral", text: "SKIPPED", help: "Not sent because a limit was reached (quiet period or hourly limit)." },
  queued: { tone: "warn", text: "QUEUED", help: "Waiting for the analysis service to pick it up." },
  running: { tone: "accent", text: "SENDING", help: "Being sent right now." },
};
export const RunStatusChip = ({ status }: { status: string }) => {
  const s = STATUS[status] ?? { tone: "neutral" as Tone, text: status.toUpperCase(), help: "" };
  return <StatusChip tone={s.tone} title={s.help} dot={status === "queued" || status === "running"}>{s.text}</StatusChip>;
};

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
  const actions = detail?.actions ?? [];
  return (
    <div className="space-y-3">
      {detail?.reason && <p className="text-sm text-text-2">{detail.reason}</p>}
      {actions.map((a, i) => (
        <div key={i} className="rounded-sm border border-border bg-background p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip tone={a.ok ? "ok" : "danger"}>{a.ok ? "OK" : "FAILED"}</StatusChip>
            <span className="font-medium">{a.type === "webhook" ? "Web service" : a.type === "mqtt" ? "Message broker" : a.type === "modbus" ? "PLC" : a.type}</span>
            {a.target && <span className="font-mono text-xs text-text-2">{a.target}</span>}
            {a.ms != null && <span className="font-mono text-xs text-text-3">{a.ms} ms</span>}
          </div>
          {a.message && <p className="mt-2 text-text-2">{a.message}</p>}
          {a.would_send !== undefined && a.would_send !== null && <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-sm bg-surface p-2 font-mono text-xs">{show(a.would_send)}</pre>}
        </div>
      ))}
      {!actions.length && !detail?.reason && <p className="text-sm text-text-3">No further detail.</p>}
    </div>
  );
}

/** Result of a preview, a test or a connection check. */
export function TestResultDialog({ state, onClose }: { state: TestState | null; onClose: () => void }) {
  return (
    <Dialog open={!!state} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto rounded-lg border-border bg-surface-2 sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{state?.title}</DialogTitle>
          <DialogDescription>
            {state?.phase === "waiting" && "Asking the analysis service. This usually takes about a second."}
            {state?.phase === "done" && <>Result: <RunStatusChip status={state.run.status} /></>}
            {state?.phase === "timeout" && "The analysis service has not picked it up yet. Check that it is running, then try again."}
            {state?.phase === "error" && state.message}
          </DialogDescription>
        </DialogHeader>
        {state?.phase === "waiting" && <p className="flex items-center gap-2 text-sm text-text-2"><Loader2 className="size-4 animate-spin" aria-hidden /> Waiting…</p>}
        {state?.phase === "done" && <ActionDetail detail={state.run.detail} />}
        <DialogFooter><Button type="button" variant="outline" onClick={onClose}>Close</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
