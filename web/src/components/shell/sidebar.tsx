"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Activity, AlertTriangle, BellRing, Inbox, LayoutGrid, LogOut, SlidersHorizontal, Users, Video, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { can, type Role } from "@/lib/roles";
import { Logo } from "@/components/console/logo";
import { useApi, api } from "@/hooks/use-api";

type Item = { href: string; label: string; icon: LucideIcon; badge?: "awaiting" | "open_incidents" };
const ITEMS: Item[] = [
  { href: "/overview", label: "Overview", icon: LayoutGrid },
  { href: "/triage", label: "Triage", icon: Inbox, badge: "awaiting" },
  { href: "/incidents", label: "Incidents", icon: AlertTriangle, badge: "open_incidents" },
  { href: "/cameras", label: "Cameras", icon: Video },
  { href: "/sources", label: "Camera setup", icon: Activity },
  { href: "/rules", label: "Rules", icon: SlidersHorizontal },
  { href: "/alerts", label: "Alerts", icon: BellRing },
  { href: "/users", label: "Users", icon: Users },
];

export function NavItem({ href, label, icon: Icon, active, badge }: Omit<Item, "badge"> & { active: boolean; badge?: number }) {
  return (
    <Link href={href} aria-current={active ? "page" : undefined}
      className={cn("flex h-9 items-center gap-3 rounded-sm border-l-2 px-3 text-sm transition-colors",
        active ? "border-brand bg-surface-2 font-medium text-foreground" : "border-transparent text-text-2 hover:bg-surface hover:text-foreground")}>
      <Icon className={cn("size-4", active && "text-brand")} aria-hidden />
      <span className="flex-1">{label}</span>
      {!!badge && <span className="font-mono text-xs text-text-2">{badge}</span>}
    </Link>
  );
}

export function Sidebar({ user }: { user: { name: string; role: Role } }) {
  const path = usePathname();
  const router = useRouter();
  const { data } = useApi<{ counts: { awaiting: number; open_incidents: number } }>("/api/overview", 10000);
  const initials = user.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  return (
    <aside className="sticky top-0 flex h-screen w-[232px] shrink-0 flex-col border-r border-border bg-surface">
      <div className="px-5 pt-6 pb-6"><Logo /></div>
      <nav className="flex-1 space-y-1 px-2.5" aria-label="Main">
        {ITEMS.filter((i) => can(user.role, i.href)).map((i) => (
          <NavItem key={i.href} {...i} active={path === i.href || path.startsWith(i.href + "/")} badge={i.badge ? data?.counts[i.badge] : undefined} />
        ))}
      </nav>
      <div className="flex items-center gap-3 border-t border-border p-4">
        <span className="flex size-8 items-center justify-center rounded-full border border-border-strong font-mono text-[11px]">{initials}</span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium">{user.name}</p>
          <p className="font-mono text-[11px] capitalize text-text-3">{user.role}</p>
        </div>
        <button aria-label="Sign out" className="text-text-2 hover:text-foreground"
          onClick={async () => { await api("/api/auth/logout", { method: "POST" }); router.push("/login"); router.refresh(); }}>
          <LogOut className="size-4" />
        </button>
      </div>
    </aside>
  );
}
