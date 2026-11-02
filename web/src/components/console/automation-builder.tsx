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
import { useT } from "@/lib/i18n/client";
import { issueLine, issueText } from "@/lib/i18n/validation";
import { K, type MessageKey } from "@/lib/i18n";

const sel = "h-9 w-full rounded-sm border border-border-strong bg-background px-2 text-sm";
const num = "h-9 border-border-strong bg-background font-mono";

/** The browser's own UTC offset, in minutes east of UTC (Jakarta = 420): what “time of day” should mean unless the admin changes it. */
export const browserOffsetMin = () => -new Date().getTimezoneOffset();
const offsetLabel = (m: number) => `UTC${m < 0 ? "−" : "+"}${String(Math.floor(Math.abs(m) / 60)).padStart(2, "0")}:${String(Math.abs(m) % 60).padStart(2, "0")}`;

type CondField = Condition["field"];
const CONDITION_MENU: { field: CondField; label: MessageKey; make: () => Condition; once: boolean }[] = [
  { field: "camera", label: K("automationBuilder.camera"), make: () => ({ field: "camera", in: [] }), once: true },
  { field: "label", label: K("automationBuilder.whatWasSeen"), make: () => ({ field: "label", in: [] }), once: true },
  { field: "severity", label: K("automationBuilder.howSeriousAtLeast"), make: () => ({ field: "severity", at_least: "HIGH" }), once: true },
  { field: "indicator", label: K("automationBuilder.aWarningSignAppeared"), make: () => ({ field: "indicator", triggered: "*" }), once: false },
  { field: "classified_as", label: K("automationBuilder.theAiCheckSays"), make: () => ({ field: "classified_as", is: "person" }), once: true },
  { field: "speed_kmh", label: K("automationBuilder.speedAbove"), make: () => ({ field: "speed_kmh", above: 60 }), once: true },
  { field: "suspicious", label: K("automationBuilder.markedSuspicious"), make: () => ({ field: "suspicious", is: true }), once: true },
  { field: "time", label: K("automationBuilder.timeOfDay"), make: () => ({ field: "time", between: ["22:00", "06:00"], utc_offset_min: browserOffsetMin() }), once: true },
];

type ActType = "webhook" | "mqtt" | "coil" | "register";
const ACTION_MENU: { id: ActType; label: MessageKey; kind: ConnectionRow["kind"]; make: (connection: string) => Action }[] = [
  { id: "webhook", label: K("automationBuilder.sendToAWebService"), kind: "webhook", make: (connection) => ({ type: "webhook", connection }) },
  { id: "mqtt", label: K("automationBuilder.publishAMessage"), kind: "mqtt", make: (connection) => ({ type: "mqtt", connection, topic: "", retain: false }) },
  { id: "coil", label: K("automationBuilder.switchAPlcOutputOn"), kind: "modbus", make: (connection) => ({ type: "modbus", connection, function: "write_coil", address: 0, hold_s: 5 }) },
  { id: "register", label: K("automationBuilder.writeAPlcValue"), kind: "modbus", make: (connection) => ({ type: "modbus", connection, function: "write_register", address: 0, value: 1 }) },
];
const actType = (a: Action): ActType => (a.type === "modbus" ? (a.function === "write_coil" ? "coil" : "register") : a.type);

const SEEN_PRESETS: { text: MessageKey; labels: string[] }[] = [{ text: K("automationBuilder.people"), labels: ["person"] }, { text: K("automationBuilder.vehicles"), labels: ["car", "truck", "bus", "motorcycle"] }];
const numOr = (s: string, d = 0) => (s.trim() === "" || Number.isNaN(Number(s)) ? d : Number(s));

function Check({ id, checked, onChange, children }: { id: string; checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <Label htmlFor={id} className="flex cursor-pointer items-center gap-2 rounded-sm border border-border-strong px-3 py-2 text-sm font-normal">
      <Checkbox id={id} checked={checked} onCheckedChange={(v) => onChange(!!v)} />{children}
    </Label>
  );
}

function ConditionEditor({ c, onChange, cameras, categories, rules }: { c: Condition; onChange: (c: Condition) => void; cameras: SourceRow[]; categories: CategoryRow[]; rules: BehaviorRule[] }) {
  const t = useT();
  switch (c.field) {
    case "camera":
      return (
        <div className="space-y-2"><p className="text-sm font-medium">{t("automationBuilder.camera")}</p>
          <div className="grid grid-cols-2 gap-2">{cameras.map((s) => (
            <Check key={s.id} id={`cond-cam-${s.id}`} checked={c.in.includes(s.id)} onChange={(v) => onChange({ ...c, in: v ? [...c.in, s.id] : c.in.filter((x) => x !== s.id) })}>{s.name} <span className="font-mono text-xs text-text-3">{s.code}</span></Check>
          ))}{!cameras.length && <p className="text-sm text-text-2">{t("automationBuilder.noCamerasYet")}</p>}</div></div>
      );
    case "label":
      return (
        <div className="space-y-2"><p className="text-sm font-medium">{t("automationBuilder.whatWasSeen")}</p>
          <div className="flex gap-2">{SEEN_PRESETS.map((p) => <Button key={p.text} type="button" size="sm" variant="outline" onClick={() => onChange({ ...c, in: [...new Set([...c.in, ...p.labels])] })}>{t(p.text)}</Button>)}
            <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ ...c, in: [] })}>{t("automationBuilder.clear")}</Button></div>
          <div className="grid grid-cols-3 gap-2">{categories.filter((k) => k.enabled).map((k) => (
            <Check key={k.label} id={`cond-label-${k.label}`} checked={c.in.includes(k.label)} onChange={(v) => onChange({ ...c, in: v ? [...c.in, k.label] : c.in.filter((x) => x !== k.label) })}>{k.title}</Check>
          ))}</div></div>
      );
    case "severity":
      return <div className="space-y-2"><Label htmlFor="cond-sev">{t("automationBuilder.howSeriousAtLeast2")}</Label>
        <select id="cond-sev" value={c.at_least} onChange={(e) => onChange({ ...c, at_least: e.target.value as typeof c.at_least })} className={sel}>
          <option value="LOW">{t("automationBuilder.low")}</option><option value="MEDIUM">{t("automationBuilder.medium")}</option><option value="HIGH">{t("automationBuilder.high")}</option><option value="CRITICAL">{t("automationBuilder.critical")}</option></select></div>;
    case "indicator":
      return <div className="space-y-2"><Label>{t("automationBuilder.aWarningSignAppeared")}</Label>
        <select aria-label={t("automationBuilder.warningSign")} value={c.triggered} onChange={(e) => onChange({ ...c, triggered: e.target.value })} className={sel}>
          <option value="*">{t("automationBuilder.anyWarningSign")}</option>{rules.map((r) => <option key={r.key} value={r.key}>{r.title}</option>)}</select>
        <p className="text-xs text-text-3">{t("automationBuilder.warningSignsAreForA")}</p></div>;
    case "classified_as":
      return <div className="space-y-2"><Label htmlFor="cond-ai">{t("automationBuilder.theAiCheckSaysIt")}</Label>
        <select id="cond-ai" value={c.is} onChange={(e) => onChange({ ...c, is: e.target.value as typeof c.is })} className={sel}>
          <option value="person">{t("automationBuilder.aPerson")}</option><option value="vehicle">{t("automationBuilder.aVehicle")}</option><option value="false_alarm">{t("automationBuilder.notARealDetection")}</option><option value="other">{t("automationBuilder.anotherKindOfObject")}</option></select></div>;
    case "speed_kmh":
      return <div className="space-y-2"><Label htmlFor="cond-speed">{t("automationBuilder.speedAboveKmH")}</Label>
        <Input id="cond-speed" type="number" min={0} max={500} value={c.above} onChange={(e) => onChange({ ...c, above: numOr(e.target.value) })} className={num} />
        <p className="text-xs text-text-3">{t("automationBuilder.needsTheSpeedSetupOf")}</p></div>;
    case "suspicious":
      return <div className="space-y-2"><Label htmlFor="cond-susp">{t("automationBuilder.markedSuspicious")}</Label>
        <select id="cond-susp" value={String(c.is)} onChange={(e) => onChange({ ...c, is: e.target.value === "true" })} className={sel}><option value="true">{t("automationBuilder.yesItHasAWarning")}</option><option value="false">{t("automationBuilder.no")}</option></select></div>;
    case "time":
      return (
        <div className="space-y-2"><p className="text-sm font-medium">{t("automationBuilder.timeOfDay")}</p>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1"><Label htmlFor="cond-from" className="text-xs text-text-2">{t("automationBuilder.from")}</Label><Input id="cond-from" type="time" value={c.between[0]} onChange={(e) => onChange({ ...c, between: [e.target.value, c.between[1]] })} className={num} /></div>
            <div className="space-y-1"><Label htmlFor="cond-to" className="text-xs text-text-2">{t("automationBuilder.until")}</Label><Input id="cond-to" type="time" value={c.between[1]} onChange={(e) => onChange({ ...c, between: [c.between[0], e.target.value] })} className={num} /></div>
            <div className="space-y-1"><Label htmlFor="cond-tz" className="text-xs text-text-2">{t("automationBuilder.siteTimeZoneHoursFrom")}</Label><Input id="cond-tz" type="number" step={0.5} min={-14} max={14} value={c.utc_offset_min / 60} onChange={(e) => onChange({ ...c, utc_offset_min: Math.round(numOr(e.target.value) * 60) })} className={num} /></div>
          </div>
          <p className="text-xs text-text-3">{t("automationBuilder.localTimeAtTheSite")}{offsetLabel(c.utc_offset_min)}{t("automationBuilder.takenFromYourBrowserIt")}</p></div>
      );
  }
}

function ActionEditor({ a, onChange, connections }: { a: Action; onChange: (a: Action) => void; connections: ConnectionRow[] }) {
  const t = useT();
  const at = actType(a);
  const menu = ACTION_MENU.find((m) => m.id === at)!;
  const options = connections.filter((c) => c.kind === menu.kind && c.enabled);
  const where = (
    <div className="space-y-2"><Label>{t("automationBuilder.whereToSendIt")}</Label>
      <select aria-label={t("automationBuilder.connection")} value={a.connection} onChange={(e) => onChange({ ...a, connection: e.target.value })} className={sel}>
        {!options.some((c) => c.id === a.connection) && <option value={a.connection}>{t("automationBuilder.chooseAConnection")}</option>}
        {options.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
  );
  if (a.type === "webhook") return (
    <div className="space-y-3">{where}
      <TemplateField id={`act-body-${a.connection}`} label={t("automationBuilder.messageLeaveEmptyForThe")} value={a.body ?? ""} onChange={(v) => onChange({ ...a, body: v || undefined })} placeholder='{"text": "{{camera.name}}: {{event.label}}"}' /></div>
  );
  if (a.type === "mqtt") return (
    <div className="space-y-3">{where}
      <div className="space-y-2"><Label htmlFor={`act-topic-${a.connection}`}>{t("automationBuilder.topicToPublishOn")}</Label><Input id={`act-topic-${a.connection}`} value={a.topic} onChange={(e) => onChange({ ...a, topic: e.target.value })} placeholder="plant/alarm" className={num} /></div>
      <TemplateField id={`act-payload-${a.connection}`} label={t("automationBuilder.messageLeaveEmptyForThe")} value={a.payload ?? ""} onChange={(v) => onChange({ ...a, payload: v || undefined })} placeholder="{{camera.name}}: {{event.label}}" />
      <div className="flex items-center justify-between gap-4 text-sm"><Label htmlFor={`act-retain-${a.connection}`} className="font-normal">{t("automationBuilder.keepTheLastMessageOn")}</Label><Switch id={`act-retain-${a.connection}`} checked={a.retain} onCheckedChange={(v) => onChange({ ...a, retain: v })} /></div></div>
  );
  if (a.function === "write_coil") return (
    <div className="space-y-3">{where}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2"><Label htmlFor={`act-addr-${a.connection}`}>{t("automationBuilder.outputNumber0To65535")}</Label><Input id={`act-addr-${a.connection}`} type="number" min={0} max={65535} value={a.address} onChange={(e) => onChange({ ...a, address: numOr(e.target.value) })} className={num} /></div>
        <div className="space-y-2"><Label htmlFor={`act-hold-${a.connection}`}>{t("automationBuilder.staysOnFor1To")}</Label><Input id={`act-hold-${a.connection}`} type="number" min={1} max={60} value={a.hold_s} onChange={(e) => onChange({ ...a, hold_s: numOr(e.target.value, 1) })} className={num} /></div></div>
      <p role="note" className="border-l-2 border-sev-high bg-sev-high/10 p-3 text-xs text-text-2">{t("automationBuilder.theOutputSwitchesOnThen")}</p></div>
  );
  return (
    <div className="space-y-3">{where}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2"><Label htmlFor={`act-raddr-${a.connection}`}>{t("automationBuilder.registerNumber0To65535")}</Label><Input id={`act-raddr-${a.connection}`} type="number" min={0} max={65535} value={a.address} onChange={(e) => onChange({ ...a, address: numOr(e.target.value) })} className={num} /></div>
        <div className="space-y-2"><Label htmlFor={`act-val-${a.connection}`}>{t("automationBuilder.valueToWrite0To")}</Label><Input id={`act-val-${a.connection}`} type="number" min={0} max={65535} value={a.value} onChange={(e) => onChange({ ...a, value: numOr(e.target.value) })} className={num} /></div></div>
      <p className="text-xs text-text-3">{t("automationBuilder.writtenOnceDoNotUse")}</p></div>
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
  const t = useT();
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
    conds.forEach((c, i) => { const r = conditionSchema.safeParse(c); if (!r.success) { const iss = r.error.issues[0]; out.push(t("automationBuilder.conditionN", { n: i + 1, msg: iss.code === "too_small" && (iss as { origin?: string }).origin === "array" ? t("automationBuilder.chooseAtLeastOne") : issueText(t, iss) })); } });
    acts.forEach((a, i) => { const r = actionSchema.safeParse(a); if (!r.success) out.push(t("automationBuilder.actionN", { n: i + 1, msg: issueLine(t, r.error.issues[0]) })); });
    acts.forEach((a, i) => { if (!connections.some((c) => c.id === a.connection && c.enabled)) out.push(t("automationBuilder.chooseConnectionOn", { n: i + 1 })); });
    if (!name.trim()) out.push(t("automationBuilder.giveName"));
    if (!acts.length) out.push(t("automationBuilder.addAction"));
    if (!out.length) { const r = automationInput.safeParse({ ...payload, mode: "simulate" }); if (!r.success) out.push(...r.error.issues.map((i) => issueLine(t, i))); }
    return out;
  }, [conds, acts, name, connections, cooldown, perHour, t]); // eslint-disable-line react-hooks/exhaustive-deps

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
      toast.success(automation ? t("common.savedToast", { name }) : t("automationBuilder.savedPractice", { name }), { description: automation ? undefined : t("automationBuilder.itIsSwitchedOffSwitch") });
      onSaved(); onOpenChange(false);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto rounded-lg border-border bg-surface-2 sm:max-w-3xl">
        <form onSubmit={save} className="space-y-5" noValidate>
          <DialogHeader>
            <DialogTitle>{automation ? t("common.editName", { name: automation.name }) : t("automationBuilder.newAlert")}</DialogTitle>
            <DialogDescription>{t("automationBuilder.sayWhenItShouldHappen")}</DialogDescription>
          </DialogHeader>
          {err && <p role="alert" className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 px-3 py-2 text-sm text-sev-critical">{err}</p>}

          <div className="grid grid-cols-[1fr_1fr] gap-3">
            <div className="space-y-2"><Label htmlFor="au-name">{t("automationBuilder.name")}</Label><Input id="au-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("automationBuilder.nightPersonAtTheDock")} className="border-border-strong bg-background" /></div>
            <div className="space-y-2"><Label htmlFor="au-desc">{t("automationBuilder.noteOptional")}</Label><Textarea id="au-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={1} className="border-border-strong bg-background" /></div>
          </div>

          <p aria-live="polite" className="rounded-sm border border-brand/40 bg-surface p-4 text-[15px] leading-relaxed">{describeAutomation(conds, acts, cameras, connections, rules, t)}</p>

          <section className="space-y-3">
            <h3 className="caps border-b border-border pb-2">{t("automationBuilder.when")}</h3>
            {conds.length === 0 && (
              <p role="alert" className="flex gap-2 border-l-2 border-sev-high bg-sev-high/10 p-3 text-sm"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-sev-high" aria-hidden />
                <span><span className="font-semibold">{t("automationBuilder.thisWillFireForEvery")}</span> {t("automationBuilder.addAConditionToNarrow")}</span></p>
            )}
            {conds.map((c, i) => (
              <div key={i} className="flex gap-3 rounded-sm border border-border bg-surface p-3">
                <div className="min-w-0 flex-1"><ConditionEditor c={c} onChange={(n) => setConds(conds.map((x, j) => (j === i ? n : x)))} cameras={cameras} categories={categories} rules={rules} /></div>
                <Button type="button" variant="ghost" size="icon" aria-label={t("automationBuilder.removeThisCondition")} onClick={() => setConds(conds.filter((_, j) => j !== i))}><X className="size-4" aria-hidden /></Button>
              </div>
            ))}
            <div className="flex items-center gap-2"><Plus className="size-4 text-text-3" aria-hidden />
              <select aria-label={t("automationBuilder.addACondition")} value={condPick} onChange={(e) => addCond(e.target.value)} className="h-9 rounded-sm border border-border-strong bg-background px-2 text-sm">
                <option value="">{t("automationBuilder.addACondition2")}</option>
                {CONDITION_MENU.map((m) => <option key={m.field} value={m.field} disabled={m.once && taken.has(m.field)}>{t(m.label)}</option>)}</select>
              <span className="text-xs text-text-3">{t("automationBuilder.allConditionsMustBeTrue")}</span></div>
          </section>

          <section className="space-y-3">
            <h3 className="caps border-b border-border pb-2">{t("automationBuilder.then")}</h3>
            {connections.filter((c) => c.enabled).length === 0 && <p className="text-sm text-text-2">{t("automationBuilder.thereIsNowhereToSend")}</p>}
            {acts.map((a, i) => (
              <div key={i} className="flex gap-3 rounded-sm border border-border bg-surface p-3">
                <div className="min-w-0 flex-1 space-y-3"><p className="text-sm font-medium">{t(ACTION_MENU.find((m) => m.id === actType(a))!.label)}</p><ActionEditor a={a} onChange={(n) => setActs(acts.map((x, j) => (j === i ? n : x)))} connections={connections} /></div>
                <Button type="button" variant="ghost" size="icon" aria-label={t("automationBuilder.removeThisAction")} onClick={() => setActs(acts.filter((_, j) => j !== i))}><X className="size-4" aria-hidden /></Button>
              </div>
            ))}
            <div className="flex items-center gap-2"><Plus className="size-4 text-text-3" aria-hidden />
              <select aria-label={t("automationBuilder.addAnAction")} value={actPick} onChange={(e) => addAct(e.target.value)} className="h-9 rounded-sm border border-border-strong bg-background px-2 text-sm">
                <option value="">{t("automationBuilder.addAnAction2")}</option>
                {ACTION_MENU.map((m) => <option key={m.id} value={m.id} disabled={!connections.some((c) => c.kind === m.kind && c.enabled)}>{t(m.label)}{connections.some((c) => c.kind === m.kind && c.enabled) ? "" : t("automationBuilder.addThatConnectionFirst")}</option>)}</select></div>
          </section>

          <section className="space-y-3">
            <h3 className="caps border-b border-border pb-2">{t("automationBuilder.limits")}</h3>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><Label htmlFor="au-cool">{t("automationBuilder.waitAtLeastThisLong")}</Label><Input id="au-cool" type="number" min={0} max={86400} value={cooldown} onChange={(e) => setCooldown(e.target.value)} className={num} /></div>
              <div className="space-y-2"><Label htmlFor="au-hour">{t("automationBuilder.atMostThisManyAlerts")}</Label><Input id="au-hour" type="number" min={1} max={1000} value={perHour} onChange={(e) => setPerHour(e.target.value)} className={num} /></div>
            </div>
            <p className="text-xs text-text-3">{t("automationBuilder.extraAlertsAreNotSent")}</p>
          </section>

          {problems.length > 0 && <ul className="space-y-1 text-xs text-text-2">{problems.map((p) => <li key={p}>· {p}</li>)}</ul>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("automationBuilder.cancel")}</Button>
            <Button type="submit" disabled={busy || problems.length > 0}>{automation ? t("automationBuilder.saveChanges") : t("automationBuilder.saveInPracticeMode")}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
