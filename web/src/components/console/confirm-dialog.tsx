"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useT } from "@/lib/i18n/client";

/** Generic confirm dialog; `children` render the extra field(s) (note, reason picker...). */
export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel, variant = "default", onConfirm, children }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: React.ReactNode; confirmLabel: string;
  variant?: "default" | "outline" | "destructive"; onConfirm: () => Promise<void> | void; children?: React.ReactNode;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const run = async () => { setBusy(true); try { await onConfirm(); onOpenChange(false); } finally { setBusy(false); } };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md rounded-lg border-border bg-surface-2">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {children}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t("confirmDialog.cancel")}</Button>
          <Button variant={variant} disabled={busy} onClick={run}>{confirmLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
