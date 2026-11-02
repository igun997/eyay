import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Toaster } from "@/components/ui/sonner";
import { LangProvider } from "@/lib/i18n/client";
import { getLang, getT } from "@/lib/i18n/server";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getT();
  return { title: "EyeAI", description: t("meta.description") };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const lang = await getLang();
  return (
    <html lang={lang} className={`dark ${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        <LangProvider lang={lang}>
          {children}
          <Toaster theme="dark" />
        </LangProvider>
      </body>
    </html>
  );
}
