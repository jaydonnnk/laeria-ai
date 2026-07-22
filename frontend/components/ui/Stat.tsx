"use client";

import { useEffect, useRef, useState } from "react";
import { countUp } from "../../lib/motion";
import { cn } from "../../lib/cn";

/** A labelled figure. The number counts up on mount (reduced-motion safe). */
export function Stat({
  value,
  label,
  prefix = "",
  suffix = "",
  decimals = 2,
  sub,
  className,
}: {
  value: number;
  label: string;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  sub?: React.ReactNode;
  className?: string;
}) {
  const [display, setDisplay] = useState(0);
  const done = useRef(false);

  useEffect(() => {
    if (done.current) {
      setDisplay(value);
      return;
    }
    done.current = true;
    const tween = countUp(value, (v) => setDisplay(v), { decimals });
    return () => {
      tween?.kill();
    };
  }, [value, decimals]);

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <span className="eyebrow">{label}</span>
      <span className="tnum text-2xl font-medium text-ink leading-none">
        {prefix}
        {display.toLocaleString(undefined, {
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        })}
        {suffix}
      </span>
      {sub && <span className="tnum text-xs text-ink-subtle">{sub}</span>}
    </div>
  );
}
