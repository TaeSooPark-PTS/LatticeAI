import { RotateCcw } from "lucide-react";
import type { ApiResult, ReviewItem } from "@/api/client";
import { ActionButton, KeyValueList } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/store/appStore";
import {
  formatSnoozedUntil,
  hasRunBefore,
  isActionableReview,
  reviewSourceDetail,
  reviewSourceLabel,
  reviewStatusVariant,
  type ReviewAction,
} from "./reviewHelpers";

type ReviewCardProps = {
  item: ReviewItem;
  feedback?: string;
  onAction: (item: ReviewItem, action: ReviewAction, hadRunBefore?: boolean) => Promise<ApiResult<ReviewItem>>;
};

export function ReviewCard({ item, feedback, onAction }: ReviewCardProps) {
  const mode = useAppStore((state) => state.mode);
  const provenance = item.provenance || {};
  const payload = item.payload || {};
  const hadRun = hasRunBefore(item);
  const snoozed = item.effective_status === "snoozed";
  const actionable = isActionableReview(item);

  return (
    <div className="rounded-lg border border-border bg-background/55 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium">{item.title}</div>
          {item.summary ? <p className="mt-1 text-sm leading-6 text-muted-foreground">{item.summary}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="muted">{reviewSourceLabel(item.source)}</Badge>
          <Badge variant={reviewStatusVariant(item.effective_status)}>{item.effective_status}</Badge>
        </div>
      </div>

      {snoozed ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-muted/24 p-3 text-sm">
          <div>
            <div className="font-medium">{formatSnoozedUntil(item.snoozed_until)}</div>
            <p className="mt-1 text-muted-foreground">This stays out of the pending queue until then. Unsnooze brings it back immediately.</p>
          </div>
          <Button size="sm" variant="outline" onClick={() => onAction(item, "unsnooze")} disabled={!actionable}>
            <RotateCcw className="h-3.5 w-3.5" /> Unsnooze
          </Button>
        </div>
      ) : null}

      {mode !== "basic" ? (
        <div className="mt-3">
          <KeyValueList
            data={{
              workflow: provenance.workflow_id,
              trigger: provenance.trigger_id,
              run: payload.last_run_id || provenance.run_id,
              source_detail: reviewSourceDetail(provenance, item.source),
              snoozed_until: item.snoozed_until,
              created_at: item.created_at,
              updated_at: item.updated_at,
            }}
            limit={8}
          />
        </div>
      ) : null}

      {actionable ? (
        <div className="mt-4 grid gap-2">
          <p className="text-xs leading-5 text-muted-foreground">
            Run now previews the action without approving it. Approve or dismiss when the result looks right.
          </p>
          <div className="flex flex-wrap gap-2" aria-label="Review actions">
            <ActionButton
              label="Run now"
              successLabel={hadRun ? "Regenerated" : "Executed"}
              action={() => onAction(item, "run_now", hadRun)}
              invalidate={[]}
            />
            <ActionButton label="Approve" action={() => onAction(item, "approve")} invalidate={[]} />
            {!snoozed ? <ActionButton label="Snooze 1 day" action={() => onAction(item, "snooze")} invalidate={[]} /> : null}
            <ActionButton label="Dismiss" action={() => onAction(item, "dismiss")} invalidate={[]} variant="destructive" />
          </div>
        </div>
      ) : null}
      {feedback ? (
        <p className="mt-2 text-xs text-emerald-300">{feedback} - item stays open until you approve or dismiss.</p>
      ) : null}
    </div>
  );
}
