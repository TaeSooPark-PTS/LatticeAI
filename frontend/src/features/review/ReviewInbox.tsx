import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ApiResult, latticeApi, type ReviewItem, type ReviewSourceFilter, type ReviewStatusFilter } from "@/api/client";
import { EmptyState, LoadingPanel, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { ReviewCard } from "./ReviewCard";
import {
  defaultSnoozeUntil,
  reviewSourceFilters,
  reviewStatusFilters,
  type ReviewAction,
} from "./reviewHelpers";

export function ReviewInbox() {
  const language = useAppStore((state) => state.language);
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = React.useState<ReviewStatusFilter>("pending");
  const [sourceFilter, setSourceFilter] = React.useState<ReviewSourceFilter>("all");
  const [runFeedback, setRunFeedback] = React.useState<Record<string, string>>({});
  const reviews = useQuery({
    queryKey: ["automationReviews", statusFilter, sourceFilter],
    queryFn: () => latticeApi.automationReviews({
      ...(statusFilter !== "all" ? { status: statusFilter } : {}),
      ...(sourceFilter !== "all" ? { source: sourceFilter } : {}),
    }),
  });
  const items = reviews.data?.data.items || [];
  const proposalCounts = useQuery({
    queryKey: ["proposalCounts"],
    queryFn: latticeApi.proposalCounts,
  });
  const pendingProposals = Number(proposalCounts.data?.data?.pending || 0);

  const actOnReview = async (
    item: ReviewItem,
    action: ReviewAction,
    hadRunBefore = false,
    reason?: string,
  ) => {
    // change_proposal reject goes through the proposal surface so the
    // rejection reason lands in the item's provenance (audit trail).
    const rejectProposal = () =>
      latticeApi.rejectProposal(item.id, reason || "") as unknown as Promise<ApiResult<ReviewItem>>;
    const call =
      action === "approve" ? () => latticeApi.approveReviewItem(item.id) :
      action === "dismiss" ? (item.source === "change_proposal" ? rejectProposal : () => latticeApi.dismissReviewItem(item.id)) :
      action === "snooze" ? () => latticeApi.snoozeReviewItem(item.id, defaultSnoozeUntil()) :
      action === "unsnooze" ? () => latticeApi.unsnoozeReviewItem(item.id) :
      () => latticeApi.runNowReviewItem(item.id);
    const result = await call();
    if (!result.ok) {
      setRunFeedback((prev) => ({
        ...prev,
        [item.id]: result.error || t(language, "review.action.failed", { action }),
      }));
      return result;
    }
    if (result.ok) {
      if (action === "run_now") {
        const payload = result.data.payload || {};
        const provenance = result.data.provenance || {};
        const runId = String(payload.last_run_id || provenance.run_id || "");
        setRunFeedback((prev) => ({
          ...prev,
          [item.id]: runId
            ? `${hadRunBefore ? t(language, "review.regenerated") : t(language, "review.executed")} · ${runId}`
            : hadRunBefore ? t(language, "review.regenerated") : t(language, "review.executed"),
        }));
      } else {
        setRunFeedback((prev) => {
          const next = { ...prev };
          delete next[item.id];
          return next;
        });
      }
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["automationReviews"] }),
        qc.invalidateQueries({ queryKey: ["proposalCounts"] }),
        qc.invalidateQueries({ queryKey: ["pendingProposals"] }),
      ]);
    }
    return result;
  };

  if (reviews.isLoading) return <LoadingPanel title={t(language, "review.inbox.title")} />;

  return (
    <Card>
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>{t(language, "review.inbox.title")}</CardTitle>
            <CardDescription>{t(language, "review.inbox.description")}</CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {pendingProposals > 0 ? (
              <Badge variant="warning" data-testid="proposal-count-badge">
                {t(language, "review.proposal.badge", { count: pendingProposals })}
              </Badge>
            ) : null}
            {reviews.data ? (
              <Badge variant={reviews.data.ok ? "success" : "warning"}>{reviews.data.ok ? t(language, "review.status.connected") : t(language, "review.status.unavailable")}</Badge>
            ) : null}
          </div>
        </div>
        <div className="grid gap-2">
          <Tabs
            tabs={reviewStatusFilters.map((filter) => ({ id: filter.id, label: t(language, filter.labelKey) }))}
            value={statusFilter}
            onChange={(id) => setStatusFilter(id as ReviewStatusFilter)}
          />
          <Tabs
            tabs={reviewSourceFilters.map((filter) => ({ id: filter.id, label: t(language, filter.labelKey) }))}
            value={sourceFilter}
            onChange={(id) => setSourceFilter(id as ReviewSourceFilter)}
          />
        </div>
      </CardHeader>
      <CardContent>
        {reviews.isError || (reviews.data && !reviews.data.ok) ? (
          <EmptyState
            title={t(language, "review.inbox.loadError")}
            detail={reviews.data?.error || t(language, "review.inbox.unavailable")}
          />
        ) : !items.length ? (
          <EmptyState
            title={t(language, "review.inbox.empty")}
            detail={statusFilter === "snoozed" ? t(language, "review.inbox.emptySnoozed") : t(language, "review.inbox.emptyPending")}
          />
        ) : (
          <div className="grid gap-3">
            {items.map((item) => (
              <ReviewCard
                key={item.id}
                item={item}
                feedback={runFeedback[item.id]}
                onAction={actOnReview}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
