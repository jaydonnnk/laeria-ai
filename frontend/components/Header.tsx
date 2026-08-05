"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { supabase } from "../lib/supabase";
import { api } from "../lib/api";
import { cn } from "../lib/cn";

// ownerOnly entries touch the agent wallet, the Stripe cardholder or the
// storefront session — all single global instances, so the backend refuses
// them for anyone but the owner. Showing them to other accounts would just
// offer a 403.
const NAV = [
  { href: "/decision", label: "Worth it?" },
  { href: "/research", label: "How it went" },
  { href: "/monitor", label: "Monitor" },
  { href: "/commerce", label: "Commerce", ownerOnly: true },
  { href: "/actions", label: "Actions", ownerOnly: true },
];

// App chrome. Hidden on the cinematic landing and the login page (they carry
// their own minimal wordmark). Sticky, hairline, mono wordmark, jade active tab.
export function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  // null while unknown — hide owner-only tabs until we know, so a guest never
  // sees them flash in and then vanish.
  const [isOwner, setIsOwner] = useState<boolean | null>(null);

  // Hooks cannot be skipped by the early return below, so the guard has to be
  // inside the effect. Without it the header still called /me on the landing
  // and login pages — and api.request() bounces to /login on a 401, so every
  // signed-out visitor was thrown off the landing page before they could read
  // it. Never call an authenticated endpoint without a session.
  const isPublic =
    pathname === "/" || pathname === "/login" || pathname === "/signup" ||
    pathname.startsWith("/auth/");

  useEffect(() => {
    if (isPublic) return;
    supabase.auth.getSession().then(({ data }) => {
      setEmail(data.session?.user?.email ?? null);
      if (!data.session) {
        setIsOwner(false);
        return;
      }
      api.me().then(
        (m) => setIsOwner(m.is_owner),
        () => setIsOwner(false)
      );
    });
  }, [pathname, isPublic]);

  if (isPublic) return null;

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/login");
  }

  const links = NAV.filter((item) => !item.ownerOnly || isOwner).map((item) => {
    const active = pathname.startsWith(item.href);
    return (
      <Link
        key={item.href}
        href={item.href}
        aria-current={active ? "page" : undefined}
        className={cn(
          "px-3 py-1.5 rounded-[--radius-sm] transition-colors whitespace-nowrap",
          active
            ? "text-ink bg-accent-soft"
            : "text-ink-muted hover:text-ink hover:bg-surface-2"
        )}
      >
        {item.label}
      </Link>
    );
  });

  return (
    <header className="sticky top-0 z-40 bg-bg/90 border-b border-hairline supports-[backdrop-filter]:bg-bg/70 supports-[backdrop-filter]:backdrop-blur-sm">
      <div className="max-w-[1100px] mx-auto px-6 h-14 flex items-center justify-between gap-6">
        <Link href="/" className="font-mono font-medium tracking-tight text-ink shrink-0">
          laeria<span className="text-accent">.</span>
        </Link>
        <nav className="hidden md:flex items-center gap-1 text-sm">{links}</nav>
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
      {/* Below md the row above has no room for five labels, so the nav moves
          to its own scrollable row. Hiding it entirely (the previous
          behaviour) stranded phone users on whatever page they landed on. */}
      <nav className="nav-scroll md:hidden flex items-center gap-1 text-sm px-4 pb-2 overflow-x-auto">
        {links}
      </nav>
    </header>
  );
}
