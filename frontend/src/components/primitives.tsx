import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Loader2, LockKeyhole, Sparkles } from "lucide-react";
import type { ApiResult } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppStore } from "@/store/appStore";
import { t } from "@/i18n";
import { cn, asArray, fmtNumber, isRecord, shortId, titleize } from "@/lib/utils";

export function SourceBadge({ result }: { result?: Pick<ApiResult, "source" | "ok" | "status"> }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  if (!result) return <Badge variant="muted">{t(language, "ui.status.notLoaded")}</Badge>;
  if (result.source === "live" && result.ok) {
    return <Badge variant="success">{mode === "basic" ? t(language, "ui.status.ready") : t(language, "ui.status.connected")}</Badge>;
  }
  return <Badge variant="warning">{mode === "basic" ? t(language, "ui.status.needsSetup") : t(language, "ui.status.unavailable")}</Badge>;
}

export function EmptyState({ title, detail }: { title?: string; detail?: React.ReactNode }) {
  const language = useAppStore((state) => state.language);
  return (
    <div className="product-empty-state flex min-h-36 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/24 p-6 text-center text-sm text-muted-foreground">
      <div className="grid h-10 w-10 place-items-center rounded-md border border-border bg-card">
        <Sparkles className="h-5 w-5 text-primary" />
      </div>
      <div className="text-base font-semibold text-foreground">{title || t(language, "ui.empty.title")}</div>
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
  const language = useAppStore((state) => state.language);
  return (
    <Card className={cn("data-panel overflow-hidden", className)}>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        {mode === "basic" ? null : <SourceBadge result={result} />}
      </CardHeader>
      <CardContent>
        {result?.ok ? children(result.data) : (
          <EmptyState detail={mode === "basic" ? t(language, "ui.empty.basicDetail") : result?.error || t(language, "ui.empty.advancedDetail")} />
        )}
      </CardContent>
    </Card>
  );
}

export function LoadingPanel({ title }: { title: string }) {
  const language = useAppStore((state) => state.language);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t(language, "ui.loading")}
        </div>
      </CardContent>
    </Card>
  );
}

export function StatGrid({ stats }: { stats: Array<{ label: string; value: unknown; hint?: string }> }) {
  return (
    <div className="data-stat-grid grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
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

function scalarText(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return Number.isFinite(value) ? fmtNumber(value) : "-";
  if (typeof value === "boolean") return value ? "Enabled" : "Disabled";
  return String(value);
}

const BASIC_HIDDEN_KEY = /(^id$|_id$|token|secret|passphrase|fingerprint|public_key|private_key|dsn|schema|endpoint|base_url|localhost|127\.0\.0\.1|stack|trace|raw|runtime|engine|module|port|host|api|internal)/i;

function hideInBasic(key: string) {
  return BASIC_HIDDEN_KEY.test(key);
}

function humanText(value: unknown) {
  const text = scalarText(value);
  if (text === "-") return text;
  if (text.includes("/") || text.includes("@") || /\.[a-z0-9]{2,5}$/i.test(text)) return text;
  return titleize(text.replace(/^agent:/i, "").replace(/^tool:/i, ""));
}

function firstRecordList(value: Record<string, unknown>) {
  const preferred = [
    "documents", "sources", "items", "agents", "workflows", "runs", "events",
    "permissions", "models", "peers", "invitations", "roles", "policies",
    "hooks", "tools", "templates", "plugins", "recent_events",
  ];
  for (const key of preferred) {
    const rows = asArray<Record<string, unknown>>(value[key]);
    if (rows.length) return rows;
  }
  return [];
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
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const rows = Object.entries(data || {})
    .filter(([key]) => mode !== "basic" || !hideInBasic(key))
    .slice(0, limit);
  if (!rows.length) return <EmptyState title={t(language, "ui.noValues")} />;
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
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  if (mode === "basic") return <FriendlySummary value={value} titleKey={titleKey} metaKey={metaKey} limit={limit} />;
  if (Array.isArray(value)) {
    if (!value.length) return <EmptyState detail={t(language, "ui.empty.listDetail")} />;
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

export function FriendlySummary({
  value,
  titleKey = "title",
  metaKey = "status",
  limit = 6,
}: {
  value: unknown;
  titleKey?: string;
  metaKey?: string;
  limit?: number;
}) {
  const language = useAppStore((state) => state.language);
  if (Array.isArray(value)) {
    if (!value.length) return <EmptyState detail={t(language, "ui.empty.listDetail")} />;
    if (value.every((item) => isRecord(item))) {
      return <EntityList items={value} titleKey={titleKey} metaKey={metaKey} limit={limit} />;
    }
    return (
      <div className="flex flex-wrap gap-1 rounded-md border border-border bg-background/55 p-3">
        {value.slice(0, limit).map((item, index) => <Badge key={`${String(item)}-${index}`} variant="muted">{humanText(item)}</Badge>)}
        {value.length > limit ? <Badge variant="muted">+{value.length - limit}</Badge> : null}
      </div>
    );
  }
  if (isRecord(value)) {
    const list = firstRecordList(value);
    if (list.length) return <EntityList items={list} titleKey={titleKey} metaKey={metaKey} limit={limit} />;
    const friendly = Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !hideInBasic(key))
        .map(([key, item]) => [key, Array.isArray(item) ? `${fmtNumber(item.length)} items` : isRecord(item) ? "available" : item]),
    );
    return <KeyValueList data={friendly} limit={limit} />;
  }
  return (
    <div className="rounded-md border border-border bg-background/55 p-3 text-sm">
      {humanText(value)}
    </div>
  );
}

export function OperationResult({
  result,
  successLabel,
}: {
  result?: ApiResult<unknown> | null;
  successLabel?: string;
}) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  if (!result) return null;
  if (!result.ok) {
    return <EmptyState title={t(language, "ui.requestUnavailable")} detail={result.error || <ValuePreview value={result.data} />} />;
  }
  return (
    <div className="space-y-2 rounded-md border border-border bg-background p-3">
      <Badge variant="success">{successLabel || t(language, "ui.requestCompleted")}</Badge>
      {mode === "basic" ? <FriendlySummary value={result.data} /> : <StructuredView value={result.data} />}
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
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const rows = asArray<Record<string, unknown>>(items).slice(0, limit);
  if (!rows.length) return <EmptyState detail={t(language, "ui.empty.listDetail")} />;
  return (
    <div className="entity-list grid gap-2">
      {rows.map((item, index) => (
        <div key={String(item.id || item.name || index)} className="entity-list-row rounded-lg border border-border bg-background/55 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-medium">{mode === "basic" ? humanText(item[titleKey] || item.name || item.label || `Item ${index + 1}`) : String(item[titleKey] || item.name || item.id || `Record ${index + 1}`)}</div>
            <Badge variant="muted">{mode === "basic" ? humanText(item[metaKey] || item.status || item.state || "ready") : String(item[metaKey] || item.status || item.state || "record")}</Badge>
          </div>
          {item.summary || item.description || item.path || (item.id && item[titleKey] !== item.id) ? (
            <p className="mt-1 text-sm text-muted-foreground">{String(item.summary || item.description || item.path || item.id)}</p>
          ) : null}
          {mode !== "basic" && item.id && item[titleKey] !== item.id ? (
            <div className="mt-1 text-xs text-muted-foreground">{shortId(item.id, 48)}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

export function ModeGate({
  title,
  detail,
  target = "advanced",
}: {
  title?: string;
  detail?: string;
  target?: "advanced" | "admin";
}) {
  const setMode = useAppStore((state) => state.setMode);
  const language = useAppStore((state) => state.language);
  return (
    <Card>
      <CardContent className="flex flex-col items-start gap-3 p-6">
        <div className="grid h-10 w-10 place-items-center rounded-md border border-border bg-background/70">
          <LockKeyhole className="h-5 w-5 text-primary" />
        </div>
        <div>
          <div className="text-lg font-semibold">{title || t(language, "ui.modeGate.title")}</div>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{detail || t(language, "ui.modeGate.detail")}</p>
        </div>
        <Button onClick={() => setMode(target)}>
          {target === "admin" ? t(language, "ui.modeGate.admin") : t(language, "ui.modeGate.advanced")}
        </Button>
      </CardContent>
    </Card>
  );
}

export function ActionButton({
  label,
  successLabel,
  action,
  onSuccess,
  invalidate,
  variant = "outline",
  disabled,
}: {
  label: string;
  successLabel?: string;
  action: () => Promise<ApiResult<unknown>>;
  onSuccess?: (result: ApiResult<unknown>) => void | Promise<void>;
  invalidate?: string[];
  variant?: React.ComponentProps<typeof Button>["variant"];
  disabled?: boolean;
}) {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const resolvedSuccessLabel = successLabel || t(language, "ui.done");
  const [result, setResult] = React.useState<string | null>(null);
  const mut = useMutation({
    mutationFn: action,
    onSuccess: async (res) => {
      setResult(res.ok ? resolvedSuccessLabel : res.error || t(language, "ui.status.unavailable"));
      await onSuccess?.(res);
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
        <span className={cn("inline-flex items-center gap-1 text-xs", result === resolvedSuccessLabel ? "text-success" : "text-warning")}>
          {result === resolvedSuccessLabel ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
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
  const tabRefs = React.useRef<Array<HTMLButtonElement | null>>([]);

  const moveFocus = (currentIndex: number, direction: number) => {
    const nextIndex = (currentIndex + direction + tabs.length) % tabs.length;
    tabRefs.current[nextIndex]?.focus();
    onChange(tabs[nextIndex].id);
  };

  return (
    <div className="product-tabs inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-muted/28 p-1" role="tablist">
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          ref={(element) => { tabRefs.current[index] = element; }}
          type="button"
          role="tab"
          aria-selected={value === tab.id}
          tabIndex={value === tab.id ? 0 : -1}
          onClick={() => onChange(tab.id)}
          onKeyDown={(event) => {
            if (event.key === "ArrowRight" || event.key === "ArrowDown") {
              event.preventDefault();
              moveFocus(index, 1);
            } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
              event.preventDefault();
              moveFocus(index, -1);
            } else if (event.key === "Home") {
              event.preventDefault();
              tabRefs.current[0]?.focus();
              onChange(tabs[0].id);
            } else if (event.key === "End") {
              event.preventDefault();
              tabRefs.current[tabs.length - 1]?.focus();
              onChange(tabs[tabs.length - 1].id);
            }
          }}
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
