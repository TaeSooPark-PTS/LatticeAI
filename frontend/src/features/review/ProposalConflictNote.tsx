import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileWarning, RotateCcw } from "lucide-react";

import { t, type Language } from "@/i18n";
import { rebaseProposal, type RebaseOutcome } from "./proposalRebase";

// Inline explanation + recovery for a 409 "base changed" proposal approval:
// the file drifted since staging, so the only honest path is to re-read the
// current file and stage a fresh proposal against it. Shared by the pending
// proposals panel and the Review Center card.
export function ProposalConflictNote({
  language,
  itemId,
  onResolved,
}: {
  language: Language;
  itemId: string;
  onResolved?: (outcome: RebaseOutcome) => void;
}) {
  const qc = useQueryClient();
  const [outcome, setOutcome] = React.useState<RebaseOutcome | null>(null);
  const rebase = useMutation({
    mutationFn: () => rebaseProposal(itemId),
    onSuccess: async (result) => {
      setOutcome(result);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["pendingProposals"] }),
        qc.invalidateQueries({ queryKey: ["proposalCounts"] }),
        qc.invalidateQueries({ queryKey: ["automationReviews"] }),
        qc.invalidateQueries({ queryKey: ["reviewItems"] }),
      ]);
      onResolved?.(result);
    },
  });

  return (
    <div className="proposal-conflict-note" role="alert" data-testid="proposal-conflict-note">
      <div className="proposal-conflict-head">
        <FileWarning className="h-4 w-4" aria-hidden="true" />
        <strong>{t(language, "proposals.conflict.title")}</strong>
      </div>
      <p>{t(language, "proposals.conflict.detail")}</p>
      {outcome === "rebased" ? (
        <p className="proposal-conflict-done" role="status">{t(language, "proposals.conflict.rebased")}</p>
      ) : outcome === "already_applied" ? (
        <p className="proposal-conflict-done" role="status">{t(language, "proposals.conflict.alreadyApplied")}</p>
      ) : (
        <button
          type="button"
          className="proposal-conflict-rebase"
          disabled={rebase.isPending}
          onClick={() => rebase.mutate()}
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          {rebase.isPending
            ? t(language, "proposals.conflict.rebasing")
            : t(language, "proposals.conflict.rebase")}
        </button>
      )}
      {rebase.isError ? (
        <small className="proposal-conflict-error">
          {t(language, "proposals.conflict.failed", {
            reason: rebase.error instanceof Error ? rebase.error.message : String(rebase.error),
          })}
        </small>
      ) : null}
    </div>
  );
}
