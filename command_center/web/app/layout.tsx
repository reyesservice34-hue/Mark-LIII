import type { ReactNode } from "react";
import "@/design/base.css";
import "@/design/ops-wall.css";

export const metadata = { title: "Mia Command Center" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="de">
      <body>{children}</body>
    </html>
  );
}
