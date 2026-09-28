import type { ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import { ConsoleProvider } from "@/components/ConsoleProvider";
import "./globals.css";

export const metadata = { title: "Deep Research — console" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ConsoleProvider>
          <AppShell>{children}</AppShell>
        </ConsoleProvider>
      </body>
    </html>
  );
}
