import { cn } from "../../lib/cn";

export function Card({
  className,
  interactive,
  ...props
}: { interactive?: boolean } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "bg-surface border border-hairline rounded-[--radius-lg] shadow-sm",
        interactive &&
          "transition-shadow transition-transform duration-200 hover:shadow-md hover:-translate-y-0.5",
        className
      )}
      {...props}
    />
  );
}
