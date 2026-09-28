import type { ReactNode } from "react";
import "./globals.css";

export const metadata = { title: "Deep Research — console" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="app" id="app" data-sidebar="expanded">
          <div className="main">
            <div className="viewport" id="viewport">{children}</div>
          </div>
        </div>
      </body>
    </html>
  );
}
