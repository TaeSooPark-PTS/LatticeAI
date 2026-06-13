import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Loader2, Sparkles } from "lucide-react";
import type { ApiResult } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppStore } from "@/store/appStore";
import { cn, asArray, fmtNumber, shortId, titleize } from "@/lib/utils";

export function SourceBadge({ result }: { result?: Pick<ApiResult, "source" | "ok" | "status"> }) {
  const mode = useAppStore((state) => state.mode);
  if (!result) return <Badge variant="muted">not loaded</Badge>;
  if (result.source === "live" && result.ok) return <Badge variant="success">{mode === "basic" ? "ready" : "connected"}</Badge>;
  return <Badge variant="warning">{mode === "basic" ? "needs setup" : "unavailable"}</Badge>;
}

export function EmptyState({ title = "Unavailable", detail }: { title?: string; detail?: React.ReactNode }) {
  return (
    <div className="flex min-h-36 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/24 p-6 text-center text-sm text-muted-foreground">
      <div className="grid h-10 w-10 place-items-center rounded-md border border-border bg-card">
        <Sparkles className="h-5 w-5 text-primary" />
      </div>
      <div className="text-base font-semibold text-foreground">{title}</div>
      {detail ? <div className="max-w-md leading-6">{detail}</div> : null}
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
  const mode = useAppStore((state) => state.mode);
  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        <SourceBadge result={result} />
      </CardHeader>
      <CardContent>
        {result?.ok ? children(result.data) : (
          <EmptyState detail={mode === "basic" ? "This area needs setup or is not available yet." : result?.error || "This capability is not reporting right now."} />
        )}
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
        <div key={stat.label} className="rounded-lg border border-border bg-background/55 p-4">
          <div className="text-xs uppercase text-muted-foreground">{stat.label}</div>
          <div className="mt-2 text-2xl font-semibold leading-tight">{typeof stat.value === "number" ? fmtNumber(stat.value) : String(stat.value ?? "-")}</div>
          {stat.hint ? <div className="mt-2 text-xs leading-5 text-muted-foreground">{stat.hint}</div> : null}
        </div>
      ))}
    </div>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function scalarText(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return Number.isFinite(value) ? fmtNumber(value) : "-";
  if (typeof value === "boolean") return value ? "Enabled" : "Disabled";
  return String(value);
}

export function ValuePreview({ value }: { value: unknown }) {
  if (typeof value === "boolean") {
    return <Badge variant={value ? "success" : "muted"}>{value ? "enabled" : "disabled"}</Badge>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <span className="text-muted-foreground">None</span>;
    const primitive = value.every((item) => item === null || ["string", "number", "boolean"].includes(typeof item));
    if (primitive) {
      return (
        <span className="flex flex-wrap gap-1">
          {value.slice(0, 5).map((item, index) => <Badge key={`${String(item)}-${index}`} variant="muted">{scalarText(item)}</Badge>)}
          {value.length > 5 ? <Badge variant="muted">+{value.length - 5}</Badge> : null}
        </span>
      );
    }
    return <span className="text-muted-foreground">{fmtNumber(value.length)} records</span>;
  }
  if (isRecord(value)) {
    const keys = Object.keys(value);
    if (!keys.length) return <span className="text-muted-foreground">No fields</span>;
    return <span className="text-muted-foreground">{keys.slice(0, 4).map(titleize).join(", ")}{keys.length > 4 ? ` +${keys.length - 4}` : ""}</span>;
  }
  const text = scalarText(value);
  return <span className="break-words">{text.length > 96 ? shortId(text, 96) : text}</span>;
}

export function KeyValueList({ data, limit = 8 }: { data: Record<string, unknown>; limit?: number }) {
  const rows = Object.entries(data || {}).slice(0, limit);
  if (!rows.length) return <EmptyState title="No values" />;
  return (
    <div className="divide-y divide-border rounded-md border border-border">
      {rows.map(([key, value]) => (
        <div key={key} className="grid grid-cols-[minmax(9rem,0.5fr)_1fr] gap-3 p-3 text-sm">
          <span className="font-medium text-muted-foreground">{titleize(key)}</span>
          <span className="min-w-0 break-words"><ValuePreview value={value} /></span>
        </div>
      ))}
    </div>
  );
}

export function StructuredView({
  value,
  titleKey = "title",
  metaKey = "status",
  limit = 8,
}: {
  value: unknown;
  titleKey?: string;
  metaKey?: string;
  limit?: number;
}) {
  if (Array.isArray(value)) {
    if (!value.length) return <EmptyState title="Nothing here yet" detail="New items will appear here when Lattice has something to show." />;
    if (value.every((item) => isRecord(item))) {
      return <EntityList items={value} titleKey={titleKey} metaKey={metaKey} limit={limit} />;
    }
    return (
      <div className="flex flex-wrap gap-1 rounded-md border border-border bg-background p-3">
        {value.slice(0, limit).map((item, index) => <Badge key={`${String(item)}-${index}`} variant="muted">{scalarText(item)}</Badge>)}
        {value.length > limit ? <Badge variant="muted">+{value.length - limit}</Badge> : null}
      </div>
    );
  }
  if (isRecord(value)) return <KeyValueList data={value} limit={limit} />;
  return (
    <div className="rounded-md border border-border bg-background p-3 text-sm">
      <ValuePreview value={value} />
    </div>
  );
}

export function OperationResult({
  result,
  successLabel = "Request completed",
}: {
  result?: ApiResult<unknown> | null;
  successLabel?: string;
}) {
  if (!result) return null;
  if (!result.ok) {
    return <EmptyState title="Request unavailable" detail={result.error || <ValuePreview value={result.data} />} />;
  }
  return (
    <div className="space-y-2 rounded-md border border-border bg-background p-3">
      <Badge variant="success">{successLabel}</Badge>
      <StructuredView value={result.data} />
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
  if (!rows.length) return <EmptyState title="Nothing here yet" detail="New items will appear here when Lattice has something to show." />;
  return (
    <div className="grid gap-2">
      {rows.map((item, index) => (
        <div key={String(item.id || item.name || index)} className="rounded-lg border border-border bg-background/55 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-medium">{String(item[titleKey] || item.name || item.id || `Record ${index + 1}`)}</div>
            <Badge variant="muted">{String(item[metaKey] || item.status || item.state || "record")}</Badge>
          </div>
          {item.summary || item.description || item.path || (item.id && item[titleKey] !== item.id) ? (
            <p className="mt-1 text-sm text-muted-foreground">{String(item.summary || item.description || item.path || item.id)}</p>
          ) : null}
          {item.id && item[titleKey] !== item.id ? (
            <div className="mt-1 text-xs text-muted-foreground">{shortId(item.id, 48)}</div>
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
    <div className="inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-muted/28 p-1">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={cn(
            "h-9 rounded-md px-3.5 text-sm font-semibold transition",
            value === tab.id ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:bg-card/60 hover:text-foreground",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
