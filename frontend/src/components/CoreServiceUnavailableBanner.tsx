import * as React from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";

const CORE_QUERY_KEYS = new Set([
  "memoryManager",
  "graph",
  "graphPreview",
  "chatHistory",
  "health",
]);

type CoreFailure = {
  key: string;
  error: string;
};

export function CoreServiceUnavailableBanner() {
  const language = useAppStore((state) => state.language);
  const queryClient = useQueryClient();
  const [failures, setFailures] = React.useState<CoreFailure[]>(() => collectFailures(queryClient));

  React.useEffect(() => queryClient.getQueryCache().subscribe(() => {
    setFailures(collectFailures(queryClient));
  }), [queryClient]);

  if (!failures.length) return null;

  const detail = failures[0]?.error || t(language, "ui.status.unavailable");
  return (
    <aside className="core-service-unavailable" role="alert" data-testid="service-unavailable-banner">
      <AlertTriangle className="h-4 w-4" aria-hidden="true" />
      <div>
        <strong>{t(language, "service.unavailable.title")}</strong>
        <span>{t(language, "service.unavailable.detail", { detail })}</span>
      </div>
      <button
        type="button"
        onClick={() => void queryClient.refetchQueries({
          predicate: (query) => CORE_QUERY_KEYS.has(String(query.queryKey[0] || "")),
        })}
      >
        <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
        {t(language, "feedback.retry")}
      </button>
    </aside>
  );
}

function collectFailures(queryClient: ReturnType<typeof useQueryClient>): CoreFailure[] {
  return queryClient.getQueryCache().getAll().flatMap((query) => {
    const key = String(query.queryKey[0] || "");
    if (!CORE_QUERY_KEYS.has(key)) return [];
    const data = query.state.data;
    if (!isUnavailableResult(data)) return [];
    return [{ key, error: typeof data.error === "string" ? data.error : "" }];
  });
}

function isUnavailableResult(value: unknown): value is { ok: false; error?: unknown } {
  return typeof value === "object" && value !== null && (value as { ok?: unknown }).ok === false;
}
