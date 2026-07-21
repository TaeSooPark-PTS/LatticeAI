import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, GitPullRequestArrow, X } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ProposalConflictNote } from "@/features/review/ProposalConflictNote";
import { ProposalDiff } from "@/features/review/ReviewCard";
import { asArray } from "@/lib/utils";
import { t, type Language } from "@/i18n";

type ProposalItem = {
  id: string;
  title: string;
  summary: string;
  kind: string;
  payload: Record<string, unknown>;
};

export function PendingProposalsPanel({ language }: { language: Language }) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = React.useState(false);
  const proposalsQ = useQuery({
    queryKey: ["pendingProposals"],
    queryFn: latticeApi.proposals,
    enabled: expanded,
  });
  const invalidate = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["pendingProposals"] }),
      qc.invalidateQueries({ queryKey: ["reviewItems"] }),
    ]);
  };
  // Conflict-aware approval: the review-queue approve surface answers 409
  // with a rebase hint when the target file changed since staging (9.9.0);
  // those items get an inline explanation + "re-read & re-apply" recovery.
  const [conflictIds, setConflictIds] = React.useState<Record<string, boolean>>({});
  const approve = useMutation({
    mutationFn: (id: string) => latticeApi.approveReviewItem(id),
    onSuccess: async (result, id) => {
      if (!result.ok && result.status === 409) {
        setConflictIds((current) => ({ ...current, [id]: true }));
        return;
      }
      if (result.ok) {
        setConflictIds((current) => {
          const next = { ...current };
          delete next[id];
          return next;
        });
        await invalidate();
      }
    },
  });
  const reject = useMutation({
    mutationFn: (id: string) => latticeApi.rejectProposal(id),
    onSuccess: invalidate,
  });

  const data = (proposalsQ.data?.data || {}) as Record<string, unknown>;
  const items = asArray<ProposalItem>(data.items);
  const busy = approve.isPending || reject.isPending;
  // A failed fetch must not masquerade as "no proposals": surface a distinct,
  // friendly error state with a retry.
  const loadFailed = proposalsQ.isError || (proposalsQ.data ? !proposalsQ.data.ok : false);

  return (
    <section
      className={`brain-care-panel pending-proposals-panel ${expanded ? "is-expanded" : "is-collapsed"}`}
      aria-label={t(language, "proposals.title")}
      data-testid="pending-proposals"
    >
      <button
        className="brain-care-summary"
        type="button"
        aria-expanded={expanded}
        aria-controls="pending-proposals-details"
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="brain-care-summary-main">
          <span><GitPullRequestArrow className="h-3.5 w-3.5" /> {t(language, "proposals.title")}</span>
          <strong>{t(language, "proposals.subtitle")}</strong>
        </span>
        <ChevronDown className="brain-care-toggle h-4 w-4" aria-hidden="true" />
      </button>

      {expanded ? (
        <div id="pending-proposals-details" className="brain-care-details">
          {proposalsQ.isPending ? (
            <p className="brain-care-note">{t(language, "proposals.loading")}</p>
          ) : loadFailed ? (
            <div className="grid gap-2" role="alert">
              <p className="brain-care-note">{t(language, "proposals.error")}</p>
              {proposalsQ.data?.error ? (
                <small className="text-xs text-muted-foreground opacity-75">{proposalsQ.data.error}</small>
              ) : null}
              <button
                type="button"
                className="daily-briefing-action"
                onClick={() => void proposalsQ.refetch()}
              >
                <span>{t(language, "common.retry")}</span>
              </button>
            </div>
          ) : items.length === 0 ? (
            <p className="brain-care-note">{t(language, "proposals.empty")}</p>
          ) : (
            <div className="pending-proposals-list">
              {items.map((item) => {
                const payload = (item.payload || {}) as Record<string, unknown>;
                const diff = asArray<string>(payload.diff);
                const tier = String(payload.tier || "small");
                return (
                  <article key={item.id} className="pending-proposal">
                    <header>
                      <strong title={String(payload.path || item.title)}>
                        {String(payload.path || item.title)}
                      </strong>
                      <span className="pending-proposal-tier">
                        {t(language, item.kind === "file_delete"
                          ? "proposals.kind.delete"
                          : tier === "large" ? "proposals.tier.large" : "proposals.tier.small")}
                      </span>
                    </header>
                    <p className="brain-care-note">{item.summary}</p>
                    {diff.length > 0 ? (
                      <ProposalDiff language={language} diff={diff} />
                    ) : null}
                    {conflictIds[item.id] ? (
                      <ProposalConflictNote language={language} itemId={item.id} />
                    ) : null}
                    <div className="pending-proposal-actions">
                      <button
                        type="button"
                        className="daily-briefing-action"
                        disabled={busy}
                        onClick={() => approve.mutate(item.id)}
                      >
                        <Check className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>{t(language, "proposals.approve")}</span>
                      </button>
                      <button
                        type="button"
                        className="daily-briefing-action pending-proposal-reject"
                        disabled={busy}
                        onClick={() => reject.mutate(item.id)}
                      >
                        <X className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>{t(language, "proposals.reject")}</span>
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
          <p className="brain-care-note">{t(language, "proposals.note")}</p>
        </div>
      ) : null}
    </section>
  );
}
