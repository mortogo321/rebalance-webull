import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Rebalance Webull",
  description: "Automated portfolio rebalancing against a Webull account.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
