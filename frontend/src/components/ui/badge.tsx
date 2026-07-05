import * as React from "react";
import { cn } from "@/lib/utils";

type BadgeProps = React.HTMLAttributes<HTMLSpanElement> & {
  variant?: "default" | "success" | "warning" | "muted" | "danger";
};

const variants = {
  default: "border-primary/25 bg-primary/12 text-primary",
  success: "border-success/30 bg-success/12 text-success",
  warning: "border-warning/30 bg-warning/12 text-warning",
  muted: "border-border bg-muted/70 text-muted-foreground",
  danger: "border-destructive/30 bg-destructive/12 text-destructive",
};

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 max-w-full items-center whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-semibold leading-none",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
