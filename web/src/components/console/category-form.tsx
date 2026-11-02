"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/hooks/use-api";
import { categoryPatch, severities } from "@/lib/schemas";
import type { CategoryRow } from "@/lib/types";

const sel = "h-9 w-full rounded-sm border border-border-strong bg-background px-2 text-sm";

/** Admin: what an event of this label means. Plain-language effect of cooldown and burst is shown under each field. */
export function CategoryDialog({ open, onOpenChange, category, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; category: CategoryRow; onSaved: () => void }) {
  const [title, setTitle] = useState(category.title);
  const [severity, setSeverity] = useState(category.severity);
  const [opens, setOpens] = useState(category.opens_incident);
  const [enabled, setEnabled] = useState(category.enabled);
  const [cooldown, setCooldown] = useState(String(category.cooldown_s));
  const [burstS, setBurstS] = useState(String(category.burst_s));
  const [frames, setFrames] = useState(String(category.burst_frames));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const payload = { title, severity, opens_incident: opens, enabled, cooldown_s: Number(cooldown), burst_s: Number(burstS), burst_frames: Number(frames) };
  const check = categoryPatch.safeParse(payload);
  const problems = check.success ? [] : check.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (problems.length) { setErr(problems.join("; ")); return; }
    setBusy(true); setErr(null);
    try {
      await api(`/api/categories/${encodeURIComponent(category.label)}`, { method: "PUT", json: payload });
      toast.success(`${title} updated`, { description: "The worker applies it within about 5 seconds." });
      onSaved(); onOpenChange(false);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] sm:max-w-lg overflow-y-auto rounded-lg border-border bg-surface-2">
        <form onSubmit={save} className="space-y-4" noValidate>
          <DialogHeader>
            <DialogTitle>Edit category · {category.label}</DialogTitle>
            <DialogDescription>Applies on every camera that lists “{category.label}”. Categories only create events and incidents, never physical actions.</DialogDescription>
          </DialogHeader>
          {err && <p role="alert" className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 px-3 py-2 text-sm text-sev-critical">{err}</p>}
          <div className="space-y-2"><Label htmlFor="cat-title">Title</Label>
            <Input id="cat-title" value={title} onChange={(e) => setTitle(e.target.value)} className="border-border-strong bg-background" /></div>
          <div className="space-y-2"><Label htmlFor="cat-sev">Severity</Label>
            <select id="cat-sev" value={severity} onChange={(e) => setSeverity(e.target.value as typeof severity)} className={sel}>{severities.map((s) => <option key={s} value={s}>{s}</option>)}</select></div>
          <div className="space-y-2"><Label htmlFor="cat-cool">Cooldown (seconds)</Label>
            <Input id="cat-cool" type="number" min={0} max={86400} step={1} value={cooldown} onChange={(e) => setCooldown(e.target.value)} className="border-border-strong bg-background font-mono" />
            <p className="text-xs text-text-3">One event per label per camera per cooldown, so a person who stays in view is not reported again every second.</p></div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2"><Label htmlFor="cat-bs">Burst (seconds)</Label>
              <Input id="cat-bs" type="number" min={0} max={60} step={1} value={burstS} onChange={(e) => setBurstS(e.target.value)} className="border-border-strong bg-background font-mono" /></div>
            <div className="space-y-2"><Label htmlFor="cat-bf">Burst snapshots</Label>
              <Input id="cat-bf" type="number" min={1} max={12} step={1} value={frames} onChange={(e) => setFrames(e.target.value)} className="border-border-strong bg-background font-mono" /></div>
          </div>
          <p className="-mt-2 text-xs text-text-3">The burst takes N snapshots over S seconds (0 seconds = a single snapshot, stills never video); behaviour rules look at them.</p>
          <div className="flex items-center justify-between gap-4 text-sm"><Label htmlFor="cat-opens" className="font-normal">Open an incident immediately</Label><Switch id="cat-opens" checked={opens} onCheckedChange={setOpens} /></div>
          <div className="flex items-center justify-between gap-4 text-sm"><Label htmlFor="cat-enabled" className="font-normal">Enabled</Label><Switch id="cat-enabled" checked={enabled} onCheckedChange={setEnabled} /></div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || problems.length > 0}>Save changes</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
