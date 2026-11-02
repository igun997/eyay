"use client";
import { useRef } from "react";
import { Textarea } from "@/components/ui/textarea";
import { TEMPLATE_NAMES } from "@/lib/automations";
import { useT } from "@/lib/i18n/client";

/** A message template with an “Insert detail” picker: the chosen detail goes in at the cursor as {{name}}. */
export function TemplateField({ id, label, value, onChange, placeholder, rows = 3 }: { id: string; label: string; value: string; onChange: (v: string) => void; placeholder: string; rows?: number }) {
  const t = useT();
  const ref = useRef<HTMLTextAreaElement>(null);
  function insert(name: string) {
    if (!name) return;
    const el = ref.current, token = `{{${name}}}`;
    const [a, b] = el ? [el.selectionStart ?? value.length, el.selectionEnd ?? value.length] : [value.length, value.length];
    onChange(value.slice(0, a) + token + value.slice(b));
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(a + token.length, a + token.length); });
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={id} className="text-sm font-medium">{label}</label>
        <select aria-label={t("templateField.insertDetail")} value="" onChange={(e) => insert(e.target.value)} className="h-8 rounded-sm border border-border-strong bg-background px-2 text-xs">
          <option value="">{t("templateField.insertDetail2")}</option>
          {TEMPLATE_NAMES.map((n) => <option key={n.name} value={n.name}>{t(n.label)}</option>)}
        </select>
      </div>
      <Textarea id={id} ref={ref} value={value} rows={rows} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} className="border-border-strong bg-background font-mono text-xs" />
    </div>
  );
}
