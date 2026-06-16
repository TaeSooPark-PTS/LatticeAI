import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi, type ReviewItem, type ReviewSourceFilter, type ReviewStatusFilter } from "@/api/client";
import { EmptyState, LoadingPanel, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ReviewCard } from "./ReviewCard";
import {
  defaultSnoozeUntil,
  reviewSourceFilters,
  reviewStatusFilters,
  type ReviewAction,
} from "./reviewHelpers";

export function ReviewInbox() {
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

  const actOnReview = async (
    item: ReviewItem,
    action: ReviewAction,
    hadRunBefore = false,
  ) => {
    const call =
      action === "approve" ? () => latticeApi.approveReviewItem(item.id) :
      action === "dismiss" ? () => latticeApi.dismissReviewItem(item.id) :
      action === "snooze" ? () => latticeApi.snoozeReviewItem(item.id, defaultSnoozeUntil()) :
      action === "unsnooze" ? () => latticeApi.unsnoozeReviewItem(item.id) :
      () => latticeApi.runNowReviewItem(item.id);
    const result = await call();
    if (!result.ok) {
      setRunFeedback((prev) => ({
        ...prev,
        [item.id]: result.error || `${action} failed`,
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
          [item.id]: runId ? `${hadRunBefore ? "Regenerated" : "Executed"} · ${runId}` : hadRunBefore ? "Regenerated" : "Executed",
        }));
      } else {
        setRunFeedback((prev) => {
          const next = { ...prev };
          delete next[item.id];
          return next;
        });
      }
      await qc.invalidateQueries({ queryKey: ["automationReviews"] });
    }
    return result;
  };

  if (reviews.isLoading) return <LoadingPanel title="Review inbox" />;

  return (
    <Card>
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Review inbox</CardTitle>
            <CardDescription>Automation suggestions waiting for your decision. Run now executes without approving.</CardDescription>
          </div>
          {reviews.data ? (
            <Badge variant={reviews.data.ok ? "success" : "warning"}>{reviews.data.ok ? "connected" : "unavailable"}</Badge>
          ) : null}
        </div>
        <div className="grid gap-2">
          <Tabs
            tabs={reviewStatusFilters}
            value={statusFilter}
            onChange={(id) => setStatusFilter(id as ReviewStatusFilter)}
          />
          <Tabs
            tabs={reviewSourceFilters}
            value={sourceFilter}
            onChange={(id) => setSourceFilter(id as ReviewSourceFilter)}
          />
        </div>
      </CardHeader>
      <CardContent>
        {reviews.isError || (reviews.data && !reviews.data.ok) ? (
          <EmptyState
            title="Could not load review inbox"
            detail={reviews.data?.error || "The review queue is not available right now."}
          />
        ) : !items.length ? (
          <EmptyState
            title="Nothing to review"
            detail={statusFilter === "snoozed" ? "Snoozed items will appear here until they are unsnoozed or become pending again." : "When automations opt into the review queue, new suggestions will appear here."}
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
