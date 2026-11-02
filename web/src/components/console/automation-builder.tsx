"use client";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { TemplateField } from "@/components/console/template-field";
import { api } from "@/hooks/use-api";
import { actionSchema, automationInput, conditionSchema, describeAutomation, type Action, type Condition } from "@/lib/automations";
import type { AutomationRow, ConnectionRow } from "@/lib/server/automations";
import type { BehaviorRule, CategoryRow, SourceRow } from "@/lib/types";

const sel = "h-9 w-full rounded-sm border border-border-strong bg-background px-2 text-sm";
const num = "h-9 border-border-strong bg-background font-mono";

/** The browser's own UTC offset, in minutes east of UTC (Jakarta = 420): what “time of day” should mean unless the admin changes it. */
export const browserOffsetMin = () => -new Date().getTimezoneOffset();
const offsetLabel = (m: number) => `UTC${m < 0 ? "−" : "+"}${String(Math.floor(Math.abs(m) / 60)).padStart(2, "0")}:${String(Math.abs(m) % 60).padStart(2, "0")}`;

type CondField = Condition["field"];
const CONDITION_MENU: { field: CondField; label: string; make: () => Condition; once: boolean }[] = [
  { field: "camera", label: "Camera", make: () => ({ field: "camera", in: [] }), once: true },
  { field: "label", label: "What was seen", make: () => ({ field: "label", in: [] }), once: true },
  { field: "severity", label: "How serious (at least)", make: () => ({ field: "severity", at_least: "HIGH" }), once: true },
  { field: "indicator", label: "A warning sign appeared", make: () => ({ field: "indicator", triggered: "*" }), once: false },
  { field: "classified_as", label: "The AI check says", make: () => ({ field: "classified_as", is: "person" }), once: true },
  { field: "speed_kmh", label: "Speed above", make: () => ({ field: "speed_kmh", above: 60 }), once: true },
  { field: "suspicious", label: "Marked suspicious", make: () => ({ field: "suspicious", is: true }), once: true },
  { field: "time", label: "Time of day", make: () => ({ field: "time", between: ["22:00", "06:00"], utc_offset_min: browserOffsetMin() }), once: true },
];

type ActType = "webhook" | "mqtt" | "coil" | "register";
const ACTION_MENU: { id: ActType; label: string; kind: ConnectionRow["kind"]; make: (connection: string) => Action }[] = [
  { id: "webhook", label: "Send to a web service", kind: "webhook", make: (connection) => ({ type: "webhook", connection }) },
  { id: "mqtt", label: "Publish a message", kind: "mqtt", make: (connection) => ({ type: "mqtt", connection, topic: "", retain: false }) },
  { id: "coil", label: "Switch a PLC output on for a few seconds", kind: "modbus", make: (connection) => ({ type: "modbus", connection, function: "write_coil", address: 0, hold_s: 5 }) },
  { id: "register", label: "Write a PLC value", kind: "modbus", make: (connection) => ({ type: "modbus", connection, function: "write_register", address: 0, value: 1 }) },
];
const actType = (a: Action): ActType => (a.type === "modbus" ? (a.function === "write_coil" ? "coil" : "register") : a.type);

const SEEN_PRESETS: { text: string; labels: string[] }[] = [{ text: "People", labels: ["person"] }, { text: "Vehicles", labels: ["car", "truck", "bus", "motorcycle"] }];
const numOr = (s: string, d = 0) => (s.trim() === "" || Number.isNaN(Number(s)) ? d : Number(s));

function Check({ id, checked, onChange, children }: { id: string; checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <Label htmlFor={id} className="flex cursor-pointer items-center gap-2 rounded-sm border border-border-strong px-3 py-2 text-sm font-normal">
      <Checkbox id={id} checked={checked} onCheckedChange={(v) => onChange(!!v)} />{children}
    </Label>
  );
}

function ConditionEditor({ c, onChange, cameras, categories, rules }: { c: Condition; onChange: (c: Condition) => void; cameras: SourceRow[]; categories: CategoryRow[]; rules: BehaviorRule[] }) {
  switch (c.field) {
    case "camera":
      return (
        <div className="space-y-2"><p className="text-sm font-medium">Camera</p>
          <div className="grid grid-cols-2 gap-2">{cameras.map((s) => (
            <Check key={s.id} id={`cond-cam-${s.id}`} checked={c.in.includes(s.id)} onChange={(v) => onChange({ ...c, in: v ? [...c.in, s.id] : c.in.filter((x) => x !== s.id) })}>{s.name} <span className="font-mono text-xs text-text-3">{s.code}</span></Check>
          ))}{!cameras.length && <p className="text-sm text-text-2">No cameras yet.</p>}</div></div>
      );
    case "label":
      return (
        <div className="space-y-2"><p className="text-sm font-medium">What was seen</p>
          <div className="flex gap-2">{SEEN_PRESETS.map((p) => <Button key={p.text} type="button" size="sm" variant="outline" onClick={() => onChange({ ...c, in: [...new Set([...c.in, ...p.labels])] })}>{p.text}</Button>)}
            <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ ...c, in: [] })}>Clear</Button></div>
          <div className="grid grid-cols-3 gap-2">{categories.filter((k) => k.enabled).map((k) => (
            <Check key={k.label} id={`cond-label-${k.label}`} checked={c.in.includes(k.label)} onChange={(v) => onChange({ ...c, in: v ? [...c.in, k.label] : c.in.filter((x) => x !== k.label) })}>{k.title}</Check>
          ))}</div></div>
      );
    case "severity":
      return <div className="space-y-2"><Label htmlFor="cond-sev">How serious, at least</Label>
        <select id="cond-sev" value={c.at_least} onChange={(e) => onChange({ ...c, at_least: e.target.value as typeof c.at_least })} className={sel}>
          <option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option><option value="CRITICAL">Critical</option></select></div>;
    case "indicator":
      return <div className="space-y-2"><Label>A warning sign appeared</Label>
        <select aria-label="Warning sign" value={c.triggered} onChange={(e) => onChange({ ...c, triggered: e.target.value })} className={sel}>
          <option value="*">Any warning sign</option>{rules.map((r) => <option key={r.key} value={r.key}>{r.title}</option>)}</select>
        <p className="text-xs text-text-3">Warning signs are for a person to check; they are never a verdict.</p></div>;
    case "classified_as":
      return <div className="space-y-2"><Label htmlFor="cond-ai">The AI check says it is</Label>
        <select id="cond-ai" value={c.is} onChange={(e) => onChange({ ...c, is: e.target.value as typeof c.is })} className={sel}>
          <option value="person">A person</option><option value="vehicle">A vehicle</option><option value="false_alarm">Not a real detection</option><option value="other">Another kind of object</option></select></div>;
    case "speed_kmh":
      return <div className="space-y-2"><Label htmlFor="cond-speed">Speed above (km/h)</Label>
        <Input id="cond-speed" type="number" min={0} max={500} value={c.above} onChange={(e) => onChange({ ...c, above: numOr(e.target.value) })} className={num} />
        <p className="text-xs text-text-3">Needs the speed setup of that camera. Speeds are estimates.</p></div>;
    case "suspicious":
      return <div className="space-y-2"><Label htmlFor="cond-susp">Marked suspicious</Label>
        <select id="cond-susp" value={String(c.is)} onChange={(e) => onChange({ ...c, is: e.target.value === "true" })} className={sel}><option value="true">Yes, it has a warning sign</option><option value="false">No</option></select></div>;
    case "time":
      return (
        <div className="space-y-2"><p className="text-sm font-medium">Time of day</p>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1"><Label htmlFor="cond-from" className="text-xs text-text-2">From</Label><Input id="cond-from" type="time" value={c.between[0]} onChange={(e) => onChange({ ...c, between: [e.target.value, c.between[1]] })} className={num} /></div>
            <div className="space-y-1"><Label htmlFor="cond-to" className="text-xs text-text-2">Until</Label><Input id="cond-to" type="time" value={c.between[1]} onChange={(e) => onChange({ ...c, between: [c.between[0], e.target.value] })} className={num} /></div>
            <div className="space-y-1"><Label htmlFor="cond-tz" className="text-xs text-text-2">Site time zone (hours from UTC)</Label><Input id="cond-tz" type="number" step={0.5} min={-14} max={14} value={c.utc_offset_min / 60} onChange={(e) => onChange({ ...c, utc_offset_min: Math.round(numOr(e.target.value) * 60) })} className={num} /></div>
          </div>
          <p className="text-xs text-text-3">Local time at the site ({offsetLabel(c.utc_offset_min)}, taken from your browser). It can wrap past midnight, for example 22:00 to 06:00.</p></div>
      );
  }
}

function ActionEditor({ a, onChange, connections }: { a: Action; onChange: (a: Action) => void; connections: ConnectionRow[] }) {
  const t = actType(a);
  const menu = ACTION_MENU.find((m) => m.id === t)!;
  const options = connections.filter((c) => c.kind === menu.kind && c.enabled);
  const where = (
    <div className="space-y-2"><Label>Where to send it</Label>
      <select aria-label="Connection" value={a.connection} onChange={(e) => onChange({ ...a, connection: e.target.value })} className={sel}>
        {!options.some((c) => c.id === a.connection) && <option value={a.connection}>Choose a connection…</option>}
        {options.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
  );
  if (a.type === "webhook") return (
    <div className="space-y-3">{where}
      <TemplateField id={`act-body-${a.connection}`} label="Message (leave empty for the standard message)" value={a.body ?? ""} onChange={(v) => onChange({ ...a, body: v || undefined })} placeholder='{"text": "{{camera.name}}: {{event.label}}"}' /></div>
  );
  if (a.type === "mqtt") return (
    <div className="space-y-3">{where}
      <div className="space-y-2"><Label htmlFor={`act-topic-${a.connection}`}>Topic to publish on</Label><Input id={`act-topic-${a.connection}`} value={a.topic} onChange={(e) => onChange({ ...a, topic: e.target.value })} placeholder="plant/alarm" className={num} /></div>
      <TemplateField id={`act-payload-${a.connection}`} label="Message (leave empty for the standard message)" value={a.payload ?? ""} onChange={(v) => onChange({ ...a, payload: v || undefined })} placeholder="{{camera.name}}: {{event.label}}" />
      <div className="flex items-center justify-between gap-4 text-sm"><Label htmlFor={`act-retain-${a.connection}`} className="font-normal">Keep the last message on the broker</Label><Switch id={`act-retain-${a.connection}`} checked={a.retain} onCheckedChange={(v) => onChange({ ...a, retain: v })} /></div></div>
  );
  if (a.function === "write_coil") return (
    <div className="space-y-3">{where}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2"><Label htmlFor={`act-addr-${a.connection}`}>Output number (0 to 65535)</Label><Input id={`act-addr-${a.connection}`} type="number" min={0} max={65535} value={a.address} onChange={(e) => onChange({ ...a, address: numOr(e.target.value) })} className={num} /></div>
        <div className="space-y-2"><Label htmlFor={`act-hold-${a.connection}`}>Stays on for (1 to 60 seconds)</Label><Input id={`act-hold-${a.connection}`} type="number" min={1} max={60} value={a.hold_s} onChange={(e) => onChange({ ...a, hold_s: numOr(e.target.value, 1) })} className={num} /></div></div>
      <p role="note" className="border-l-2 border-sev-high bg-sev-high/10 p-3 text-xs text-text-2">The output switches on, then off again by itself. Use it for a lamp, a siren relay or a signal. Never connect it to an emergency stop, an interlock, a door release or machine control.</p></div>
  );
  return (
    <div className="space-y-3">{where}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2"><Label htmlFor={`act-raddr-${a.connection}`}>Register number (0 to 65535)</Label><Input id={`act-raddr-${a.connection}`} type="number" min={0} max={65535} value={a.address} onChange={(e) => onChange({ ...a, address: numOr(e.target.value) })} className={num} /></div>
        <div className="space-y-2"><Label htmlFor={`act-val-${a.connection}`}>Value to write (0 to 65535)</Label><Input id={`act-val-${a.connection}`} type="number" min={0} max={65535} value={a.value} onChange={(e) => onChange({ ...a, value: numOr(e.target.value) })} className={num} /></div></div>
      <p className="text-xs text-text-3">Written once. Do not use it for anything that protects people or machines.</p></div>
  );
}

/**
 * Build an alert as a sentence: “When … sees … and …, then …”. New alerts are always saved in practice mode and switched off;
 * going live is a separate, confirmed step on the list.
 */
export function AutomationDialog({ open, onOpenChange, automation, cameras, categories, rules, connections, nextPosition, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; automation?: AutomationRow; cameras: SourceRow[]; categories: CategoryRow[]; rules: BehaviorRule[];
  connections: ConnectionRow[]; nextPosition: number; onSaved: () => void;
}) {
  const [name, setName] = useState(automation?.name ?? "");
  const [description, setDescription] = useState(automation?.description ?? "");
  const [conds, setConds] = useState<Condition[]>(automation?.conditions ?? []);
  const [acts, setActs] = useState<Action[]>(automation?.actions ?? []);
  const [cooldown, setCooldown] = useState(String(automation?.cooldown_s ?? 60));
  const [perHour, setPerHour] = useState(String(automation?.max_per_hour ?? 30));
  const [condPick, setCondPick] = useState("");
  const [actPick, setActPick] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const payload = { name, description, conditions: conds, actions: acts, cooldown_s: numOr(cooldown), max_per_hour: numOr(perHour, 1), position: automation?.position ?? nextPosition };
  const problems = useMemo(() => {
    const out: string[] = [];
    conds.forEach((c, i) => { const r = conditionSchema.safeParse(c); if (!r.success) out.push(`Condition ${i + 1}: ${r.error.issues[0].message === "Too small: expected array to have >=1 items" ? "choose at least one" : r.error.issues[0].message}`); });
    acts.forEach((a, i) => { const r = actionSchema.safeParse(a); if (!r.success) out.push(`Action ${i + 1}: ${r.error.issues[0].path.join(" ") ? r.error.issues[0].path.join(" ") + ": " : ""}${r.error.issues[0].message}`); });
    acts.forEach((a, i) => { if (!connections.some((c) => c.id === a.connection && c.enabled)) out.push(`Action ${i + 1}: choose a connection that is switched on`); });
    if (!name.trim()) out.push("Give the alert a name");
    if (!acts.length) out.push("Add at least one action");
    if (!out.length) { const r = automationInput.safeParse({ ...payload, mode: "simulate" }); if (!r.success) out.push(...r.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`)); }
    return out;
  }, [conds, acts, name, connections, cooldown, perHour]); // eslint-disable-line react-hooks/exhaustive-deps

  const taken = new Set(conds.map((c) => c.field));
  const addCond = (field: string) => { const m = CONDITION_MENU.find((x) => x.field === field); if (m) setConds([...conds, m.make()]); setCondPick(""); };
  const addAct = (id: string) => {
    const m = ACTION_MENU.find((x) => x.id === id);
    const c = m && connections.find((x) => x.kind === m.kind && x.enabled);
    if (m && c) setActs([...acts, m.make(c.id)]);
    setActPick("");
  };

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (problems.length) { setErr(problems.join("; ")); return; }
    setBusy(true); setErr(null);
    try {
      if (automation) await api(`/api/automations/${automation.id}`, { method: "PUT", json: payload });
      else await api("/api/automations", { method: "POST", json: { ...payload, mode: "simulate", enabled: false } });
      toast.success(automation ? `${name} saved` : `${name} saved in practice mode`, { description: automation ? undefined : "It is switched off. Switch it on to practise with real events; go live only when you are ready." });
      onSaved(); onOpenChange(false);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto rounded-lg border-border bg-surface-2 sm:max-w-3xl">
        <form onSubmit={save} className="space-y-5" noValidate>
          <DialogHeader>
            <DialogTitle>{automation ? `Edit ${automation.name}` : "New alert"}</DialogTitle>
            <DialogDescription>Say when it should happen and what it should do. Alerts and indications only: never connect an alert to an emergency stop, an interlock, a door release or machine control.</DialogDescription>
          </DialogHeader>
          {err && <p role="alert" className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 px-3 py-2 text-sm text-sev-critical">{err}</p>}

          <div className="grid grid-cols-[1fr_1fr] gap-3">
            <div className="space-y-2"><Label htmlFor="au-name">Name</Label><Input id="au-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Night person at the dock" className="border-border-strong bg-background" /></div>
            <div className="space-y-2"><Label htmlFor="au-desc">Note (optional)</Label><Textarea id="au-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={1} className="border-border-strong bg-background" /></div>
          </div>

          <p aria-live="polite" className="rounded-sm border border-brand/40 bg-surface p-4 text-[15px] leading-relaxed">{describeAutomation(conds, acts, cameras, connections, rules)}</p>

          <section className="space-y-3">
            <h3 className="caps border-b border-border pb-2">When</h3>
            {conds.length === 0 && (
              <p role="alert" className="flex gap-2 border-l-2 border-sev-high bg-sev-high/10 p-3 text-sm"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-sev-high" aria-hidden />
                <span><span className="font-semibold">This will fire for every event.</span> Add a condition to narrow it down.</span></p>
            )}
            {conds.map((c, i) => (
              <div key={i} className="flex gap-3 rounded-sm border border-border bg-surface p-3">
                <div className="min-w-0 flex-1"><ConditionEditor c={c} onChange={(n) => setConds(conds.map((x, j) => (j === i ? n : x)))} cameras={cameras} categories={categories} rules={rules} /></div>
                <Button type="button" variant="ghost" size="icon" aria-label="Remove this condition" onClick={() => setConds(conds.filter((_, j) => j !== i))}><X className="size-4" aria-hidden /></Button>
              </div>
            ))}
            <div className="flex items-center gap-2"><Plus className="size-4 text-text-3" aria-hidden />
              <select aria-label="Add a condition" value={condPick} onChange={(e) => addCond(e.target.value)} className="h-9 rounded-sm border border-border-strong bg-background px-2 text-sm">
                <option value="">Add a condition…</option>
                {CONDITION_MENU.map((m) => <option key={m.field} value={m.field} disabled={m.once && taken.has(m.field)}>{m.label}</option>)}</select>
              <span className="text-xs text-text-3">All conditions must be true. No camera condition means any camera.</span></div>
          </section>

          <section className="space-y-3">
            <h3 className="caps border-b border-border pb-2">Then</h3>
            {connections.filter((c) => c.enabled).length === 0 && <p className="text-sm text-text-2">There is nowhere to send alerts yet. Add a connection under “Where alerts go” first.</p>}
            {acts.map((a, i) => (
              <div key={i} className="flex gap-3 rounded-sm border border-border bg-surface p-3">
                <div className="min-w-0 flex-1 space-y-3"><p className="text-sm font-medium">{ACTION_MENU.find((m) => m.id === actType(a))!.label}</p><ActionEditor a={a} onChange={(n) => setActs(acts.map((x, j) => (j === i ? n : x)))} connections={connections} /></div>
                <Button type="button" variant="ghost" size="icon" aria-label="Remove this action" onClick={() => setActs(acts.filter((_, j) => j !== i))}><X className="size-4" aria-hidden /></Button>
              </div>
            ))}
            <div className="flex items-center gap-2"><Plus className="size-4 text-text-3" aria-hidden />
              <select aria-label="Add an action" value={actPick} onChange={(e) => addAct(e.target.value)} className="h-9 rounded-sm border border-border-strong bg-background px-2 text-sm">
                <option value="">Add an action…</option>
                {ACTION_MENU.map((m) => <option key={m.id} value={m.id} disabled={!connections.some((c) => c.kind === m.kind && c.enabled)}>{m.label}{connections.some((c) => c.kind === m.kind && c.enabled) ? "" : " (add that connection first)"}</option>)}</select></div>
          </section>

          <section className="space-y-3">
            <h3 className="caps border-b border-border pb-2">Limits</h3>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><Label htmlFor="au-cool">Wait at least this long between alerts from the same camera (seconds)</Label><Input id="au-cool" type="number" min={0} max={86400} value={cooldown} onChange={(e) => setCooldown(e.target.value)} className={num} /></div>
              <div className="space-y-2"><Label htmlFor="au-hour">At most this many alerts per hour</Label><Input id="au-hour" type="number" min={1} max={1000} value={perHour} onChange={(e) => setPerHour(e.target.value)} className={num} /></div>
            </div>
            <p className="text-xs text-text-3">Extra alerts are not sent; they are listed as skipped in the activity log.</p>
          </section>

          {problems.length > 0 && <ul className="space-y-1 text-xs text-text-2">{problems.map((p) => <li key={p}>· {p}</li>)}</ul>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || problems.length > 0}>{automation ? "Save changes" : "Save in practice mode"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
