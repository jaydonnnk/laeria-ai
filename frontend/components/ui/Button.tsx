import { forwardRef } from "react";
import { cn } from "../../lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-ink hover:bg-accent-hover active:bg-accent-press shadow-xs",
  secondary:
    "bg-surface text-ink border border-hairline-strong hover:bg-surface-2",
  ghost: "bg-transparent text-ink hover:bg-surface-2",
  danger: "bg-surface text-danger border border-danger/30 hover:bg-danger-soft",
};

const SIZES: Record<Size, string> = {
  sm: "text-[13px] px-3 py-1.5 gap-1.5 rounded-[--radius-sm]",
  md: "text-sm px-4 py-2 gap-2 rounded-[--radius]",
};

export const Button = forwardRef<
  HTMLButtonElement,
  { variant?: Variant; size?: Size } & React.ButtonHTMLAttributes<HTMLButtonElement>
>(function Button({ variant = "primary", size = "md", className, ...props }, ref) {
  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center font-medium transition-colors duration-150 cursor-pointer",
        "disabled:opacity-50 disabled:cursor-not-allowed select-none",
        VARIANTS[variant],
        SIZES[size],
        className
      )}
      {...props}
    />
  );
});
