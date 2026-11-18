import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, GitPullRequestArrow, X } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ProposalDiff } from "@/features/review/ReviewCard";
import { t, type Language } from "@/i18n";
import { asArray } from "@/lib/utils";

type InlineProposal = {
  id: string;
  title: string;
  summary: string;
  kind: string;
  payload?: Record<string, unknown>;
};

export function InlineStagedProposals({ language }: { language: Language }) {
  const qc = useQueryClient();
  const [feedback, setFeedback] = React.useState<string | null>(null);
  const proposalsQ = useQuery({
    queryKey: ["pendingProposals"],
    queryFn: latticeApi.proposals,
  });

  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["pendingProposals"] }),
      qc.invalidateQueries({ queryKey: ["reviewItems"] }),
    ]);
  };

  const approve = useMutation({
    mutationFn: (id: string) => latticeApi.approveReviewItem(id),
    onSuccess: async (res) => {
      if (res.ok) {
        setFeedback(t(language, "brain.proposals.inlineApproved"));
        await refresh();
      }
    },
  });

  const reject = useMutation({
    mutationFn: (id: string) => latticeApi.rejectProposal(id),
    onSuccess: async (res) => {
      if (res.ok) {
        setFeedback(t(language, "brain.proposals.inlineRejected"));
        await refresh();
      }
    },
  });

  const data = (proposalsQ.data?.ok ? proposalsQ.data.data : {}) as Record<string, unknown>;
  const items = asArray<InlineProposal>(data.items);
  if (!items.length && !feedback) return null;
  const busy = approve.isPending || reject.isPending;

  return (
    <section
      className="mt-2 space-y-2 rounded-lg border border-border/70 bg-muted/20 p-3 text-xs"
      data-testid="inline-staged-proposals"
      aria-label={t(language, "proposals.title")}
    >
      {items.length > 0 ? (
        <>
          <div className="flex items-center gap-1.5 font-semibold text-foreground">
            <GitPullRequestArrow className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            <span>{t(language, "brain.proposals.inlineTitle", { count: items.length })}</span>
          </div>
          <div className="space-y-2">
            {items.slice(0, 3).map((item) => {
              const payload = (item.payload || {}) as Record<string, unknown>;
              const diff = asArray<string>(payload.diff);
              const label = String(payload.path || item.title);
              return (
                <article
                  key={item.id}
                  data-testid={`inline-proposal-${item.id}`}
                  className="space-y-1.5 rounded-md border border-border/60 bg-background p-2.5"
                >
                  <div className="font-semibold text-foreground">{label}</div>
                  {item.summary ? <p className="m-0 text-muted-foreground">{item.summary}</p> : null}
                  {diff.length > 0 ? <ProposalDiff language={language} diff={diff.slice(0, 12)} /> : null}
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    <button
                      type="button"
                      data-testid={`inline-proposal-approve-${item.id}`}
                      disabled={busy}
                      onClick={() => approve.mutate(item.id)}
                      className="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
                    >
                      <Check className="h-3 w-3" aria-hidden="true" />
                      {t(language, "proposals.approve")}
                    </button>
                    <button
                      type="button"
                      data-testid={`inline-proposal-reject-${item.id}`}
                      disabled={busy}
                      onClick={() => reject.mutate(item.id)}
                      className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2.5 py-1 font-medium text-foreground hover:bg-muted disabled:opacity-50"
                    >
                      <X className="h-3 w-3" aria-hidden="true" />
                      {t(language, "proposals.reject")}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </>
      ) : null}
      {feedback ? (
        <p className="m-0 font-medium text-foreground" role="status">
          {feedback}
        </p>
      ) : null}
    </section>
  );
}
