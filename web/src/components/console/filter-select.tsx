"use client";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

/** "Site  All sites ⌄" style filter: caps-less label + value inside one outlined control. */
export function FilterSelect({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[];
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={label} className="h-9 gap-2 border-border-strong bg-transparent text-[13px]">
        <span className="text-text-3">{label}</span>
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="border-border-strong bg-surface-2">
        {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}
