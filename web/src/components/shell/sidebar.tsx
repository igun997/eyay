"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Activity, AlertTriangle, BellRing, Inbox, LayoutGrid, LogOut, SlidersHorizontal, Users, Video, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { can, type Role } from "@/lib/roles";
import { Logo } from "@/components/console/logo";
import { LangSwitch } from "@/components/shell/lang-switch";
import { useApi, api } from "@/hooks/use-api";
import { useT } from "@/lib/i18n/client";
import { K, type MessageKey } from "@/lib/i18n";

type Item = { href: string; label: MessageKey; icon: LucideIcon; badge?: "awaiting" | "open_incidents" };
const ITEMS: Item[] = [
  { href: "/overview", label: K("nav.overview"), icon: LayoutGrid },
  { href: "/triage", label: K("nav.triage"), icon: Inbox, badge: "awaiting" },
  { href: "/incidents", label: K("nav.incidents"), icon: AlertTriangle, badge: "open_incidents" },
  { href: "/cameras", label: K("nav.cameras"), icon: Video },
  { href: "/sources", label: K("nav.cameraSetup"), icon: Activity },
  { href: "/rules", label: K("nav.rules"), icon: SlidersHorizontal },
  { href: "/alerts", label: K("nav.alerts"), icon: BellRing },
  { href: "/users", label: K("nav.users"), icon: Users },
];

export function NavItem({ href, label, icon: Icon, active, badge }: Omit<Item, "badge"> & { active: boolean; badge?: number }) {
  const t = useT();
  return (
    <Link href={href} aria-current={active ? "page" : undefined}
      className={cn("flex h-9 items-center gap-3 rounded-sm border-l-2 px-3 text-sm transition-colors",
        active ? "border-brand bg-surface-2 font-medium text-foreground" : "border-transparent text-text-2 hover:bg-surface hover:text-foreground")}>
      <Icon className={cn("size-4", active && "text-brand")} aria-hidden />
      <span className="flex-1">{t(label)}</span>
      {!!badge && <span className="font-mono text-xs text-text-2">{badge}</span>}
    </Link>
  );
}

export function Sidebar({ user }: { user: { name: string; role: Role } }) {
  const t = useT();
  const path = usePathname();
  const router = useRouter();
  const { data } = useApi<{ counts: { awaiting: number; open_incidents: number } }>("/api/overview", 10000);
  const initials = user.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  return (
    <aside className="sticky top-0 flex h-screen w-[232px] shrink-0 flex-col border-r border-border bg-surface">
      <div className="px-5 pt-6 pb-6"><Logo /></div>
      <nav className="flex-1 space-y-1 px-2.5" aria-label={t("nav.main")}>
        {ITEMS.filter((i) => can(user.role, i.href)).map((i) => (
          <NavItem key={i.href} {...i} active={path === i.href || path.startsWith(i.href + "/")} badge={i.badge ? data?.counts[i.badge] : undefined} />
        ))}
      </nav>
      <div className="space-y-3 border-t border-border p-4">
        <LangSwitch />
        <div className="flex items-center gap-3">
        <span className="flex size-8 items-center justify-center rounded-full border border-border-strong font-mono text-[11px]">{initials}</span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium">{user.name}</p>
          <p className="font-mono text-[11px] text-text-3">{t(user.role === "admin" ? "users.roleAdmin" : "users.roleSecurity")}</p>
        </div>
        <button aria-label={t("nav.signOut")} className="text-text-2 hover:text-foreground"
          onClick={async () => { await api("/api/auth/logout", { method: "POST" }); router.push("/login"); router.refresh(); }}>
          <LogOut className="size-4" />
        </button>
        </div>
      </div>
    </aside>
  );
}
