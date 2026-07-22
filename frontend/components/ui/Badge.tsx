import { cn } from "../../lib/cn";

type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "accent";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-ink-muted border-hairline",
  success: "bg-success-soft text-success border-success/20",
  warning: "bg-warning-soft text-warning border-warning/20",
  danger: "bg-danger-soft text-danger border-danger/20",
  info: "bg-info-soft text-info border-info/20",
  accent: "bg-accent-soft text-accent border-accent/20",
};

// Maps the app's status strings to a tone, so callers can pass a raw status.
const STATUS_TONE: Record<string, Tone> = {
  executed: "success",
  approved: "info",
  active: "success",
  issued: "warning",
  pending_approval: "warning",
  cancelled: "neutral",
  canceled: "neutral",
  failed: "danger",
  high: "success",
  moderate: "warning",
  low: "danger",
  none: "success",
  medium: "warning",
};

export function Badge({
  tone,
  status,
  dot,
  className,
  children,
}: {
  tone?: Tone;
  status?: string;
  dot?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const resolved: Tone = tone ?? (status ? STATUS_TONE[status] ?? "neutral" : "neutral");
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 border rounded-full px-2.5 py-0.5",
        "font-mono text-[11px] tracking-wide uppercase",
        TONES[resolved],
        className
      )}
    >
      {dot && <span className="w-1.5 h-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}
