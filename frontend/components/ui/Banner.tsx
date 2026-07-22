import { cn } from "../../lib/cn";

type Tone = "error" | "info" | "success";

const TONES: Record<Tone, string> = {
  error: "bg-danger-soft text-danger border-danger/20",
  info: "bg-info-soft text-info border-info/20",
  success: "bg-success-soft text-success border-success/20",
};

export function Banner({
  tone = "info",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: React.ReactNode;
}) {
  if (!children) return null;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "border rounded-[--radius] px-4 py-2.5 text-sm flex items-start gap-2",
        TONES[tone],
        className
      )}
    >
      <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-current shrink-0" />
      <span>{children}</span>
    </div>
  );
}

export function SectionHeader({
  n,
  title,
  aside,
  className,
}: {
  n?: string;
  title: string;
  aside?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-baseline gap-3 mb-4", className)}>
      {n && <span className="tnum text-accent text-sm">{n}</span>}
      <h2 className="text-lg font-semibold text-ink">{title}</h2>
      {aside && <span className="text-sm text-ink-subtle">{aside}</span>}
    </div>
  );
}
