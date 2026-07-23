"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { supabase } from "../lib/supabase";
import { cn } from "../lib/cn";

const NAV = [
  { href: "/commerce", label: "Commerce" },
  { href: "/actions", label: "Actions" },
  { href: "/decision", label: "What to buy" },
  { href: "/research", label: "How it went" },
  { href: "/monitor", label: "Monitor" },
];

// App chrome. Hidden on the cinematic landing and the login page (they carry
// their own minimal wordmark). Sticky, hairline, mono wordmark, jade active tab.
export function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setEmail(data.session?.user?.email ?? null));
  }, [pathname]);

  if (pathname === "/" || pathname === "/login") return null;

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/login");
  }

  return (
    <header className="sticky top-0 z-40 bg-bg/90 border-b border-hairline supports-[backdrop-filter]:bg-bg/70 supports-[backdrop-filter]:backdrop-blur-sm">
      <div className="max-w-[1100px] mx-auto px-6 h-14 flex items-center justify-between gap-6">
        <Link href="/" className="font-mono font-medium tracking-tight text-ink shrink-0">
          laeria<span className="text-accent">.</span>
        </Link>
        <nav className="hidden md:flex items-center gap-1 text-sm">
          {NAV.map((item) => {
            const active = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "px-3 py-1.5 rounded-[--radius-sm] transition-colors",
                  active
                    ? "text-ink bg-accent-soft"
                    : "text-ink-muted hover:text-ink hover:bg-surface-2"
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="flex items-center gap-3 shrink-0">
          {email && (
            <span className="hidden sm:inline font-mono text-[11px] text-ink-subtle max-w-[160px] truncate">
              {email}
            </span>
          )}
          <button
            onClick={signOut}
            className="font-mono text-[11px] tracking-wide uppercase text-ink-muted hover:text-ink transition-colors"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
