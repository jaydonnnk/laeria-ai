import { cn } from "../../lib/cn";

const base =
  "w-full bg-surface border border-hairline-strong rounded-[--radius] px-3 py-2 text-sm text-ink placeholder:text-ink-subtle outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-soft";

export function Input({
  className,
  mono,
  ...props
}: { mono?: boolean } & React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(base, mono && "font-mono tabular-nums", className)} {...props} />;
}

export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(base, "resize-y min-h-[92px]", className)} {...props} />;
}

export function Select({
  className,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(base, "cursor-pointer", className)} {...props} />;
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: React.ReactNode;
}) {
  return (
    <label className="grid gap-1.5">
      <span className="text-[13px] font-medium text-ink-muted">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-subtle">{hint}</span>}
    </label>
  );
}
