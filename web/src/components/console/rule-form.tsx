"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/hooks/use-api";
import { indicationKinds, severities, ruleInput, ruleKey, ruleUpdate } from "@/lib/schemas";
import { OBJECT_CHOICES, SENSITIVITY_HELP, SENSITIVITY_VALUES, sensitivityOf, sensitivityText, type Sensitivity } from "@/lib/plain";
import type { BehaviorRule, CategoryRow } from "@/lib/types";

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9_]/g, "_").slice(0, 40);
const sel = "h-9 w-full rounded-sm border border-border-strong bg-background px-2 text-sm";

/**
 * Admin: add or change a warning-sign rule. A warning sign is for a person to check, never a verdict. Three ways to spot one: a yes/no
 * question put to the optional second AI check, an object the camera AI sees next to a person, or how an object moved.
 */
export function RuleDialog({ open, onOpenChange, categories, rule, nextPosition, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; categories: CategoryRow[]; rule?: BehaviorRule; nextPosition: number; onSaved: () => void;
}) {
  const [key, setKey] = useState(rule?.key ?? "");
  const [title, setTitle] = useState(rule?.title ?? "");
  const [description, setDescription] = useState(rule?.description ?? "");
  const [kind, setKind] = useState<BehaviorRule["kind"]>(rule?.kind ?? "classifier");
  const [question, setQuestion] = useState(rule?.question ?? "");
  const [indKind, setIndKind] = useState(rule?.config?.indication_kind ?? "lingering");
  const [objects, setObjects] = useState<string[]>(rule?.config?.objects ?? ["knife"]);
  const [margin, setMargin] = useState(rule?.config?.margin ?? 0.3);
  const [minSec, setMinSec] = useState(String((rule?.config?.min_burst_ms ?? 5000) / 1000));
  const [appliesTo, setAppliesTo] = useState<string[]>(rule?.applies_to ?? ["person"]);
  const [threshold, setThreshold] = useState(rule?.threshold ?? 0.75);
  const [severity, setSeverity] = useState<BehaviorRule["severity"]>(rule?.severity ?? "HIGH");
  const [opens, setOpens] = useState(rule?.opens_incident ?? true);
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [position, setPosition] = useState(rule?.position ?? nextPosition);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const payload = {
    title, description, kind, question: kind === "classifier" ? question : null, applies_to: appliesTo, threshold, severity,
    opens_incident: opens, enabled, position, config: kind === "indication" ? { indication_kind: indKind, min_burst_ms: Math.round(Number(minSec) * 1000) } : kind === "object" ? { objects, margin } : {},
  };
  const check = rule ? ruleUpdate.safeParse(payload) : ruleInput.safeParse({ ...payload, key });
  const problems = check.success ? [] : check.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (problems.length) { setErr(problems.join("; ")); return; }
    setBusy(true); setErr(null);
    try {
      if (rule) await api(`/api/rules/${rule.key}`, { method: "PUT", json: payload });
      else await api("/api/rules", { method: "POST", json: { ...payload, key: ruleKey.parse(key) } });
      toast.success(rule ? `${title} updated` : `${title} added`, { description: "Rules are re-read every 30 seconds." });
      onSaved(); onOpenChange(false);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] sm:max-w-xl overflow-y-auto rounded-lg border-border bg-surface-2">
        <form onSubmit={save} className="space-y-4" noValidate>
          <DialogHeader>
            <DialogTitle>{rule ? `Edit ${rule.title}` : "Add a warning-sign rule"}</DialogTitle>
            <DialogDescription>A warning sign is something for a person to check. It never accuses anyone and never decides anything.</DialogDescription>
          </DialogHeader>
          {err && <p role="alert" className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 px-3 py-2 text-sm text-sev-critical">{err}</p>}
          <div className="grid grid-cols-[170px_1fr] gap-3">
            <div className="space-y-2"><Label htmlFor="rule-key">Key</Label>
              <Input id="rule-key" value={key} disabled={!!rule} onChange={(e) => setKey(slug(e.target.value))} placeholder="knife" className="border-border-strong bg-background font-mono" /></div>
            <div className="space-y-2"><Label htmlFor="rule-title">Title</Label>
              <Input id="rule-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Person holding a knife" className="border-border-strong bg-background" /></div>
          </div>
          <div className="space-y-2"><Label htmlFor="rule-desc">Description</Label>
            <Textarea id="rule-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="border-border-strong bg-background" /></div>
          <div className="space-y-2"><Label htmlFor="rule-kind">How it is spotted</Label>
            <select id="rule-kind" value={kind} disabled={!!rule} onChange={(e) => setKind(e.target.value as BehaviorRule["kind"])} className={sel}>
              <option value="classifier">Second AI check: a yes/no question about the picture</option>
              <option value="object">Spotted next to a person: an object the camera AI sees</option>
              <option value="indication">Activity pattern: how the object moved</option>
            </select></div>
          {kind === "classifier" && (
            <>
              <p role="note" className="border-l-2 border-sev-high bg-sev-high/10 p-3 text-xs text-text-2">This kind of rule needs the optional second AI check to be switched on. Without it the rule never appears.</p>
              <div className="space-y-2"><Label htmlFor="rule-q">Yes/no question</Label>
                <Textarea id="rule-q" value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} placeholder="Is the person holding a knife?" className="border-border-strong bg-background" />
                <p className="text-xs text-text-3">Phrase it so “yes” is the thing to check. How well it works depends on the AI and the camera: try it on your own pictures before relying on it.</p></div>
            </>
          )}
          {kind === "object" && (
            <>
              <p role="note" className="border-l-2 border-brand bg-surface p-3 text-xs text-text-2">Works with the cameras alone, no second AI check needed. Small or hidden objects are often missed, and everyday scenes (a kitchen, an office) can cause false warnings.</p>
              <fieldset className="space-y-2">
                <legend className="mb-1 text-sm font-medium">Objects to look for</legend>
                <div className="grid grid-cols-3 gap-2">
                  {OBJECT_CHOICES.map((o) => (
                    <Label key={o} htmlFor={`rule-obj-${o}`} className="flex cursor-pointer items-center gap-2 rounded-sm border border-border-strong px-3 py-2 font-normal">
                      <Checkbox id={`rule-obj-${o}`} checked={objects.includes(o)} onCheckedChange={(v) => setObjects((l) => (v ? [...l, o] : l.filter((x) => x !== o)))} />{o}
                    </Label>
                  ))}
                </div>
              </fieldset>
              <div className="space-y-2"><Label htmlFor="rule-margin">How close to the person <span className="ml-2 font-mono text-xs text-text-2">{margin.toFixed(2)}</span></Label>
                <input id="rule-margin" type="range" min={0} max={1} step={0.05} value={margin} onChange={(e) => setMargin(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
                <p className="text-xs text-text-3">0 = only inside the person’s box. Higher also counts objects a little further away, as a share of the person’s size.</p></div>
            </>
          )}
          {kind !== "indication" && (
            <div className="space-y-2"><Label htmlFor="rule-sens">Sensitivity</Label>
              <div className="grid grid-cols-[1fr_120px] items-center gap-3">
                <select id="rule-sens" value={sensitivityOf(threshold)} onChange={(e) => setThreshold(SENSITIVITY_VALUES[e.target.value as Sensitivity])} className={sel}>
                  {(["low", "medium", "high"] as const).map((k) => <option key={k} value={k}>{sensitivityText(SENSITIVITY_VALUES[k])}</option>)}</select>
                <Input aria-label="Exact value" title={kind === "object" ? "Minimum certainty that the object is really there" : "Minimum chance of “yes” needed for the warning sign"} type="number" min={0.05} max={1} step={0.05} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} className={`${sel} font-mono`} />
              </div>
              <p className="text-xs text-text-3">{SENSITIVITY_HELP[sensitivityOf(threshold)]} The number is the {kind === "object" ? "minimum certainty that the object is really there" : "minimum chance of “yes” needed"} (0.05 to 1): lower shows more warning signs.</p></div>
          )}
          {kind === "indication" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><Label htmlFor="rule-ik">Activity</Label>
                <select id="rule-ik" value={indKind} onChange={(e) => setIndKind(e.target.value as typeof indKind)} className={sel}>{indicationKinds.map((k) => <option key={k} value={k}>{k}</option>)}</select></div>
              <div className="space-y-2"><Label htmlFor="rule-min">For at least (seconds)</Label>
                <Input id="rule-min" type="number" min={0} max={60} step={0.5} value={minSec} onChange={(e) => setMinSec(e.target.value)} className="border-border-strong bg-background font-mono" /></div>
              <p className="col-span-2 text-xs text-text-3">Works with the cameras alone, no second AI check needed.</p>
            </div>
          )}
          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium">Applies to</legend>
            <div className="grid grid-cols-3 gap-2">
              {categories.map((c) => (
                <Label key={c.label} htmlFor={`rule-cat-${c.label}`} className="flex cursor-pointer items-center gap-2 rounded-sm border border-border-strong px-3 py-2 font-normal">
                  <Checkbox id={`rule-cat-${c.label}`} checked={appliesTo.includes(c.label)} onCheckedChange={(v) => setAppliesTo((l) => (v ? [...l, c.label] : l.filter((x) => x !== c.label)))} />{c.title}
                </Label>
              ))}
            </div>
          </fieldset>
          <div className="grid grid-cols-[1fr_110px] gap-3">
            <div className="space-y-2"><Label htmlFor="rule-sev">How serious it is when it appears</Label>
              <select id="rule-sev" value={severity} onChange={(e) => setSeverity(e.target.value as typeof severity)} className={sel}>{severities.map((s) => <option key={s} value={s}>{s}</option>)}</select></div>
            <div className="space-y-2"><Label htmlFor="rule-pos">Order</Label>
              <Input id="rule-pos" type="number" min={0} max={1000} value={position} onChange={(e) => setPosition(Number(e.target.value))} className="border-border-strong bg-background font-mono" /></div>
          </div>
          <div className="flex items-center justify-between gap-4 text-sm">
            <Label htmlFor="rule-opens" className="font-normal">Open an incident when this warning sign appears on an event that has none</Label>
            <Switch id="rule-opens" checked={opens} onCheckedChange={setOpens} />
          </div>
          <div className="flex items-center justify-between gap-4 text-sm">
            <Label htmlFor="rule-enabled" className="font-normal">Enabled</Label>
            <Switch id="rule-enabled" checked={enabled} onCheckedChange={setEnabled} />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || problems.length > 0}>{rule ? "Save changes" : "Add rule"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
