import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { ApiResult } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, asArray, fmtNumber, titleize } from "@/lib/utils";

export function SourceBadge({ result }: { result?: Pick<ApiResult, "source" | "ok" | "status"> }) {
  if (!result) return <Badge variant="muted">not loaded</Badge>;
  if (result.source === "live" && result.ok) return <Badge variant="success">live API</Badge>;
  return <Badge variant="warning">unavailable</Badge>;
}

export function EmptyState({ title = "Unavailable", detail }: { title?: string; detail?: React.ReactNode }) {
  return (
    <div className="flex min-h-28 flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border bg-muted/30 p-5 text-center text-sm text-muted-foreground">
      <AlertCircle className="h-5 w-5" />
      <div className="font-medium text-foreground">{title}</div>
      {detail ? <div>{detail}</div> : null}
    </div>
  );
}

export function DataPanel<T>({
  title,
  description,
  result,
  children,
  className,
}: {
  title: string;
  description?: string;
  result?: ApiResult<T>;
  children: (data: T) => React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        <SourceBadge result={result} />
      </CardHeader>
      <CardContent>
        {result?.ok ? children(result.data) : <EmptyState detail={result?.error || "The backend did not return this capability."} />}
      </CardContent>
    </Card>
  );
}

export function LoadingPanel({ title }: { title: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading
        </div>
      </CardContent>
    </Card>
  );
}

export function StatGrid({ stats }: { stats: Array<{ label: string; value: unknown; hint?: string }> }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {stats.map((stat) => (
        <div key={stat.label} className="rounded-md border border-border bg-background p-3">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">{stat.label}</div>
          <div className="mt-1 text-2xl font-semibold">{typeof stat.value === "number" ? fmtNumber(stat.value) : String(stat.value ?? "-")}</div>
          {stat.hint ? <div className="mt-1 text-xs text-muted-foreground">{stat.hint}</div> : null}
        </div>
      ))}
    </div>
  );
}

export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto rounded-md border border-border bg-muted/40 p-3 text-xs leading-relaxed text-muted-foreground">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function KeyValueList({ data, limit = 8 }: { data: Record<string, unknown>; limit?: number }) {
  const rows = Object.entries(data || {}).slice(0, limit);
  if (!rows.length) return <EmptyState title="No values" />;
  return (
    <div className="divide-y divide-border rounded-md border border-border">
      {rows.map(([key, value]) => (
        <div key={key} className="grid grid-cols-[minmax(9rem,0.5fr)_1fr] gap-3 p-3 text-sm">
          <span className="font-medium text-muted-foreground">{titleize(key)}</span>
          <span className="break-words">{typeof value === "object" ? JSON.stringify(value) : String(value ?? "-")}</span>
        </div>
      ))}
    </div>
  );
}

export function EntityList({
  items,
  titleKey = "title",
  metaKey = "type",
  limit = 8,
}: {
  items: unknown;
  titleKey?: string;
  metaKey?: string;
  limit?: number;
}) {
  const rows = asArray<Record<string, unknown>>(items).slice(0, limit);
  if (!rows.length) return <EmptyState title="No records" detail="The API returned an empty collection." />;
  return (
    <div className="grid gap-2">
      {rows.map((item, index) => (
        <div key={String(item.id || item.name || index)} className="rounded-md border border-border bg-background p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-medium">{String(item[titleKey] || item.name || item.id || `Record ${index + 1}`)}</div>
            <Badge variant="muted">{String(item[metaKey] || item.status || item.state || "record")}</Badge>
          </div>
          {item.summary || item.description || item.path ? (
            <p className="mt-1 text-sm text-muted-foreground">{String(item.summary || item.description || item.path)}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

export function ActionButton({
  label,
  successLabel = "Done",
  action,
  invalidate,
  variant = "outline",
  disabled,
}: {
  label: string;
  successLabel?: string;
  action: () => Promise<ApiResult<unknown>>;
  invalidate?: string[];
  variant?: React.ComponentProps<typeof Button>["variant"];
  disabled?: boolean;
}) {
  const qc = useQueryClient();
  const [result, setResult] = React.useState<string | null>(null);
  const mut = useMutation({
    mutationFn: action,
    onSuccess: async (res) => {
      setResult(res.ok ? successLabel : res.error || "Unavailable");
      if (invalidate) {
        await Promise.all(invalidate.map((key) => qc.invalidateQueries({ queryKey: [key] })));
      }
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant={variant} disabled={disabled || mut.isPending} onClick={() => mut.mutate()}>
        {mut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        {label}
      </Button>
      {result ? (
        <span className={cn("inline-flex items-center gap-1 text-xs", result === successLabel ? "text-emerald-300" : "text-amber-300")}>
          {result === successLabel ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
          {result}
        </span>
      ) : null}
    </div>
  );
}

export function Tabs({
  tabs,
  value,
  onChange,
}: {
  tabs: Array<{ id: string; label: string }>;
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1 rounded-md border border-border bg-muted/30 p-1">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={cn(
            "h-8 rounded px-3 text-sm font-medium transition",
            value === tab.id ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
