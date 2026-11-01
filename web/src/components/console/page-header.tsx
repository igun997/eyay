import { cn } from "@/lib/utils";

export function PageHeader({ title, subtitle, badge, actions, className }: {
  title: React.ReactNode; subtitle?: React.ReactNode; badge?: React.ReactNode; actions?: React.ReactNode; className?: string;
}) {
  return (
    <header className={cn("flex items-start justify-between gap-4 px-8 pt-7 pb-5", className)}>
      <div className="min-w-0">
        <div className="flex items-center gap-3">
          <h1 className="text-[26px] leading-8 font-semibold tracking-tight">{title}</h1>
          {badge}
        </div>
        {subtitle && <p className="mt-1 font-mono text-xs text-text-2">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}

export const Panel = ({ title, aside, children, className, bodyClassName }: {
  title?: string; aside?: React.ReactNode; children: React.ReactNode; className?: string; bodyClassName?: string;
}) => (
  <section className={cn("rounded-sm border border-border bg-surface", className)}>
    {title && (
      <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
        <h2 className="caps">{title}</h2>
        {aside && <div className="text-xs text-text-2">{aside}</div>}
      </div>
    )}
    <div className={bodyClassName}>{children}</div>
  </section>
);
