import { redirect } from "next/navigation";
import { getSession } from "@/lib/server/auth";
import { Sidebar } from "@/components/shell/sidebar";

export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const s = await getSession();
  if (!s) redirect("/login");
  return (
    <div className="flex min-h-screen">
      <Sidebar user={{ name: s.name, role: s.role }} />
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}
