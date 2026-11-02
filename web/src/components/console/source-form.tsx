"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/hooks/use-api";
import { kindOf } from "@/lib/schemas";
import type { CategoryRow, SourceRow } from "@/lib/types";
import { useFmt, useT } from "@/lib/i18n/client";

const slug = (s: string) => s.toUpperCase().replace(/[^A-Z0-9_-]/g, "");

/** Admin: add (or edit) a live camera and choose what it should watch for. */
export function SourceDialog({ open, onOpenChange, categories, source, onSaved, nextCode }: {
  open: boolean; onOpenChange: (o: boolean) => void; categories: CategoryRow[]; source?: SourceRow; onSaved: () => void; nextCode: string;
}) {
  const t = useT();
  const fmt = useFmt();
  const [code, setCode] = useState(source?.code ?? nextCode);
  const [name, setName] = useState(source?.name ?? "");
  const [url, setUrl] = useState(source?.url ?? "");
  const [labels, setLabels] = useState<string[]>(source?.labels ?? ["person", "car"]);
  const [conf, setConf] = useState(source?.min_confidence ?? 0.4);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const kind = kindOf(url);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      if (source) await api(`/api/sources/${source.id}`, { method: "PATCH", json: { name, url, labels, min_confidence: conf } });
      else await api("/api/sources", { method: "POST", json: { code, name, url, labels, min_confidence: conf } });
      toast.success(source ? t("common.updatedToast", { name: source.code }) : t("common.addedToast", { name: code }), { description: t("sourceForm.itIsPickedUpWithin") });
      onSaved(); onOpenChange(false);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg rounded-lg border-border bg-surface-2">
        <form onSubmit={save} className="space-y-4" noValidate>
          <DialogHeader>
            <DialogTitle>{source ? t("common.editName", { name: source.code }) : t("sourceForm.addCamera")}</DialogTitle>
            <DialogDescription>{t("sourceForm.enterTheCameraSLive")}</DialogDescription>
          </DialogHeader>
          {err && <p role="alert" className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 px-3 py-2 text-sm text-sev-critical">{err}</p>}
          <div className="grid grid-cols-[130px_1fr] gap-3">
            <div className="space-y-2"><Label htmlFor="code">{t("sourceForm.code")}</Label><Input id="code" value={code} disabled={!!source} onChange={(e) => setCode(slug(e.target.value))} className="border-border-strong bg-background font-mono" /></div>
            <div className="space-y-2"><Label htmlFor="name">{t("sourceForm.locationName")}</Label><Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("sourceForm.dock2")} className="border-border-strong bg-background" /></div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="url">{t("sourceForm.cameraAddressLiveStream")} {kind && <span className="ml-2 font-mono text-[11px] text-ok">{kind === "rtsp" ? t("sourceForm.cameraStream") : t("sourceForm.webStream")}</span>}</Label>
            <Input id="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="rtsp://user:password@host:554/stream  or  https://host/live.m3u8" className="border-border-strong bg-background font-mono text-[13px]" />
          </div>
          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium">{t("sourceForm.whatToLookFor")}</legend>
            <div className="grid grid-cols-3 gap-2">
              {categories.filter((c) => c.enabled).map((c) => (
                <Label key={c.label} htmlFor={`cat-${c.label}`} className="flex cursor-pointer items-center gap-2 rounded-sm border border-border-strong px-3 py-2 font-normal">
                  <Checkbox id={`cat-${c.label}`} checked={labels.includes(c.label)} onCheckedChange={(v) => setLabels((l) => (v ? [...l, c.label] : l.filter((x) => x !== c.label)))} />{c.title}
                </Label>
              ))}
            </div>
          </fieldset>
          <div className="space-y-2">
            <Label htmlFor="conf">{t("sourceForm.minimumCertainty")} <span className="ml-2 font-mono text-xs text-text-2">{fmt.num(conf)}</span></Label>
            <input id="conf" type="range" min={0.1} max={0.9} step={0.05} value={conf} onChange={(e) => setConf(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("sourceForm.cancel")}</Button>
            <Button type="submit" disabled={busy || !name || !url || !labels.length}>{source ? t("sourceForm.saveChanges") : t("sourceForm.addCamera")}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
