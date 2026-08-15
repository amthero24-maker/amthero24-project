import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

const indexable = process.env.NEXT_PUBLIC_SITE_INDEXABLE === "true";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "https://amthero24.de"),
  title: "AmtHero24 — Papierkram in Deutschland endlich verständlich",
  description: "Sam ist ein KI-gestützter WhatsApp-Assistent für Briefe, Termine, Kündigungen, Verträge und Rückerstattungen in Deutschland.",
  robots: indexable ? { index: true, follow: true } : { index: false, follow: false, noarchive: true },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="de"><body><a className="skip" href="#main">Zum Inhalt</a>{children}</body></html>;
}
