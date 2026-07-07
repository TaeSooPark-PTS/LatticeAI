import { RotateCcw } from "lucide-react";
import type { ApiResult, ReviewItem } from "@/api/client";
import { ActionButton, KeyValueList } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
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
  const language = useAppStore((state) => state.language);
  const provenance = item.provenance || {};
  const payload = item.payload || {};
  const hadRun = hasRunBefore(item);
  const snoozed = item.effective_status === "snoozed";
  const actionable = isActionableReview(item);
  const canRunNow = item.source !== "chat_followup";

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
            <p className="mt-1 text-muted-foreground">{t(language, "review.snoozed.detail")}</p>
          </div>
          <Button size="sm" variant="outline" onClick={() => onAction(item, "unsnooze")} disabled={!actionable}>
            <RotateCcw className="h-3.5 w-3.5" /> {t(language, "review.unsnooze")}
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
            {t(language, canRunNow ? "review.runNow.detail" : "review.chat.detail")}
          </p>
          <div className="flex flex-wrap gap-2" aria-label={t(language, "review.actions.aria")}>
            {canRunNow ? (
              <ActionButton
                label={t(language, "review.runNow")}
                successLabel={hadRun ? t(language, "review.regenerated") : t(language, "review.executed")}
                action={() => onAction(item, "run_now", hadRun)}
                invalidate={[]}
              />
            ) : null}
            <ActionButton label={t(language, "review.approve")} action={() => onAction(item, "approve")} invalidate={[]} />
            {!snoozed ? <ActionButton label={t(language, "review.snoozeDay")} action={() => onAction(item, "snooze")} invalidate={[]} /> : null}
            <ActionButton label={t(language, "review.dismiss")} action={() => onAction(item, "dismiss")} invalidate={[]} variant="destructive" />
          </div>
        </div>
      ) : null}
      {feedback ? (
        <p className={`mt-2 text-xs ${/fail|error|unavailable/i.test(feedback) ? "text-amber-300" : "text-emerald-300"}`}>
          {feedback} - {t(language, "review.feedback.open")}
        </p>
      ) : null}
    </div>
  );
}
