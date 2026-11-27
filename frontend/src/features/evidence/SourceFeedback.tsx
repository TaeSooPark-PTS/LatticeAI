import * as React from "react";
import "@/i18n/brain";
import { create } from "zustand";

import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";

export const useRejectedSources = create<{
  rejected: Record<string, string>;
  set: (key: string, correctionId: string) => void;
}>((set) => ({
  rejected: {},
  set: (key, correctionId) => set((state) => ({ rejected: { ...state.rejected, [key]: correctionId } })),
}));

export function SourceFeedback({
  language,
  nodeId,
  title,
  query,
}: {
  language: Language;
  nodeId: string;
  title: string;
  query: string;
}) {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const key = `${workspaceId || ""}:${nodeId}`;
  const correctionId = useRejectedSources((state) => state.rejected[key]);
  const remember = useRejectedSources((state) => state.set);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  const submit = async () => {
    setBusy(true);
    setError("");
    const result = correctionId
      ? await latticeApi.revertCorrection(correctionId)
      : await latticeApi.rejectCitation(nodeId, query);
    setBusy(false);
    const nextId = correctionId ? "" : result.data.correction_id || "";
    if (result.ok && (correctionId || nextId)) remember(key, nextId);
    else setError(result.error || t(language, "brain.sourceFeedback.failed"));
  };

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span aria-live="polite" className="text-muted-foreground">
        {correctionId ? t(language, "brain.sourceFeedback.marked") : ""}
      </span>
      <button
        type="button"
        className="rounded px-1 font-medium text-muted-foreground underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        disabled={busy}
        aria-label={t(language, correctionId ? "brain.sourceFeedback.undoAria" : "brain.sourceFeedback.rejectAria", { title })}
        onClick={() => void submit()}
      >
        {t(language, correctionId ? "brain.sourceFeedback.undo" : "brain.sourceFeedback.reject")}
      </button>
      {error ? <span role="alert" className="text-destructive">{error}</span> : null}
    </div>
  );
}
