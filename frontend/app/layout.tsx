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

// Leads with the research story, because that is what the product is and what
// every page except one is about. The previous title described the payment
// rail — the payoff, not the pitch — so a shared link or a row of browser
// tabs advertised the wrong product.
export const metadata: Metadata = {
  title: "laeria — what's actually worth buying, according to real discussions",
  description:
    "An AI agent that reads the communities who actually own the thing, returns an honest verdict with the evidence behind it, and shows how confident it is and why. When the consensus is strong it can buy the pick under a spending limit it can prove it obeyed.",
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
