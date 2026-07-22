import type { Metadata } from "next";
import { IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import { Header } from "../components/Header";
import "./globals.css";

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "laeria — agentic payments under a mandate",
  description:
    "An AI agent that funds, discovers, issues a disposable card, and checks out — every step bounded by a spending mandate.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
      <body>
        <Header />
        {children}
      </body>
    </html>
  );
}
