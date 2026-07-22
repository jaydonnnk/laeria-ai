"use client";

import { useEffect, useRef } from "react";
import { revealStagger } from "../lib/motion";
import { cn } from "../lib/cn";

// Consistent page container + entrance stagger for its [data-anim] children.
export function PageShell({
  title,
  eyebrow,
  intro,
  width = "wide",
  children,
}: {
  title: string;
  eyebrow?: string;
  intro?: React.ReactNode;
  width?: "wide" | "narrow";
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    revealStagger(ref.current, "[data-anim]", { stagger: 0.06 });
  }, []);

  return (
    <main
      ref={ref}
      className={cn(
        "mx-auto px-6 py-10 md:py-14",
        width === "wide" ? "max-w-[1100px]" : "max-w-[760px]"
      )}
    >
      <div className="mb-8 md:mb-10" data-anim>
        {eyebrow && <div className="eyebrow mb-3">{eyebrow}</div>}
        <h1 className="text-2xl md:text-[2rem] font-semibold tracking-[-0.02em] text-ink">
          {title}
        </h1>
        {intro && <p className="mt-3 text-ink-muted max-w-[46rem]">{intro}</p>}
      </div>
      {children}
    </main>
  );
}
