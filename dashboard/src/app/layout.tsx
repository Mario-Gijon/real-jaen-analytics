import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { SessionControl } from "@/features/access/SessionControl";
import { UpdateControl } from "@/features/updates/UpdateControl";
import { AppShell } from "@/ui/AppShell";
import "@/ui/tokens.css";
import "@/ui/base.css";

export const metadata: Metadata = {
  title: { default: "Real Jaén · Análisis", template: "%s · Real Jaén · Análisis" },
  description: "Partidos, equipos y jugadores para el analista del Real Jaén CF.",
};

export const viewport: Viewport = {
  themeColor: "#4b2677",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="es">
      <body>
        <AppShell actions={<UpdateControl />} session={<SessionControl />}>{children}</AppShell>
      </body>
    </html>
  );
}
