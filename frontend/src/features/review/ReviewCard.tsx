import * as React from "react";
import { RotateCcw } from "lucide-react";
import type { ApiResult, ReviewItem } from "@/api/client";
import { ActionButton, KeyValueList } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { ProposalConflictNote } from "./ProposalConflictNote";
import {
  formatSnoozedUntil,
  hasRunBefore,
  isActionableReview,
  reviewChangeClassLabel,
  reviewRiskLabel,
  reviewSourceDetail,
  reviewSourceLabel,
  reviewStatusLabel,
  reviewStatusVariant,
  type ReviewAction,
  type ReviewFeedback,
} from "./reviewHelpers";

type ReviewCardProps = {
  item: ReviewItem;
  feedback?: ReviewFeedback;
  onAction: (
    item: ReviewItem,
    action: ReviewAction,
    hadRunBefore?: boolean,
    reason?: string,
  ) => Promise<ApiResult<ReviewItem>>;
};

const DIFF_PREVIEW_LINES = 24;

// Framed, readable diff preview shared by the review card and the pending
// proposals panel: a plain-language header, +/- line coloring, and an honest
// "N more lines" note instead of a silent truncation.
export function ProposalDiff({
  language,
  diff,
  path,
  className = "",
}: {
  language: Language;
  diff: string[];
  path?: string;
  className?: string;
}) {
  if (!diff.length) return null;
  const visible = diff.slice(0, DIFF_PREVIEW_LINES);
  const hidden = diff.length - visible.length;
  return (
    <figure className={`m-0 grid gap-1 ${className}`}>
      <figcaption className="flex flex-wrap items-baseline gap-2 text-xs text-muted-foreground">
        <strong className="font-medium text-foreground">{t(language, "proposals.diff.header")}</strong>
        {path ? <code className="break-all">{path}</code> : null}
      </figcaption>
      <pre className="pending-proposal-diff" aria-label={t(language, "proposals.diff")}>
        {visible.map((line, index) => (
          <span key={index} className={`block ${diffLineClass(line)}`}>
            {line || " "}
          </span>
        ))}
      </pre>
      {hidden > 0 ? (
        <small className="text-xs text-muted-foreground">
          {t(language, "proposals.diff.more", { count: hidden })}
        </small>
      ) : null}
    </figure>
  );
}

function diffLineClass(line: string) {
  if (/^\+(?!\+\+)/.test(line)) return "text-success";
  if (/^-(?!--)/.test(line)) return "text-destructive";
  return "";
}

export function ReviewCard({ item, feedback, onAction }: ReviewCardProps) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [rejectReason, setRejectReason] = React.useState("");
  const provenance = item.provenance || {};
  const payload = item.payload || {};
  const hadRun = hasRunBefore(item);
  const snoozed = item.effective_status === "snoozed";
  const actionable = isActionableReview(item);
  const isProposal = item.source === "change_proposal";
  const canRunNow = item.source !== "chat_followup" && item.source !== "agent_followup" && !isProposal;
  const diff = Array.isArray(payload.diff) ? (payload.diff as string[]) : [];
  const proposalTier = String(payload.tier || "small");
  const diffPath = typeof payload.path === "string" && payload.path.trim() ? payload.path : undefined;
  const riskLabel = reviewRiskLabel(language, provenance.risk);
  const changeLabel = reviewChangeClassLabel(language, provenance.change_class);
  const toolName = typeof provenance.tool === "string" && provenance.tool.trim() ? provenance.tool : "";
  const proposedBy = typeof provenance.proposed_by === "string" && provenance.proposed_by.trim() ? provenance.proposed_by : "";

  return (
    <div className="rounded-lg border border-border bg-background/55 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium">{item.title}</div>
          {item.summary ? <p className="mt-1 text-sm leading-6 text-muted-foreground">{item.summary}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="muted">{reviewSourceLabel(language, item.source)}</Badge>
          {isProposal ? (
            <Badge variant="muted">
              {t(language, item.kind === "file_delete"
                ? "proposals.kind.delete"
                : proposalTier === "large" ? "proposals.tier.large" : "proposals.tier.small")}
            </Badge>
          ) : null}
          <Badge variant={reviewStatusVariant(item.effective_status)}>
            {reviewStatusLabel(language, item.effective_status)}
          </Badge>
        </div>
      </div>

      {isProposal && diff.length > 0 ? (
        <ProposalDiff language={language} diff={diff} path={diffPath} className="mt-3" />
      ) : null}

      {snoozed ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-muted/24 p-3 text-sm">
          <div>
            <div className="font-medium">{formatSnoozedUntil(language, item.snoozed_until)}</div>
            <p className="mt-1 text-muted-foreground">{t(language, "review.snoozed.detail")}</p>
          </div>
          <Button size="sm" variant="outline" onClick={() => onAction(item, "unsnooze")} disabled={!actionable}>
            <RotateCcw className="h-3.5 w-3.5" /> {t(language, "review.unsnooze")}
          </Button>
        </div>
      ) : null}

      {riskLabel || changeLabel || toolName || proposedBy ? (
        <div className="mt-3 grid gap-2">
          {changeLabel || riskLabel ? (
            <div className="flex flex-wrap items-center gap-2">
              {changeLabel ? <Badge variant="muted">{changeLabel}</Badge> : null}
              {riskLabel ? <Badge variant="muted">{riskLabel}</Badge> : null}
            </div>
          ) : null}
          {toolName ? (
            <p className="m-0 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{t(language, "review.summary.tool")}</span> · {toolName}
            </p>
          ) : null}
          {proposedBy ? (
            <p className="m-0 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{t(language, "review.summary.proposedBy")}</span> · {proposedBy}
            </p>
          ) : null}
        </div>
      ) : null}

      {mode !== "basic" ? (
        <details className="mt-3 rounded-md border border-border">
          <summary className="cursor-pointer select-none p-3 text-sm font-medium text-muted-foreground">
            {t(language, "review.technical.title")}
          </summary>
          <div className="p-3 pt-0">
            <KeyValueList
              data={{
                workflow: provenance.workflow_id,
                trigger: provenance.trigger_id,
                run: payload.last_run_id || provenance.run_id,
                ...(isProposal ? { path: payload.path } : {}),
                source_detail: reviewSourceDetail(language, provenance, item.source),
                snoozed_until: item.snoozed_until,
                created_at: item.created_at,
                updated_at: item.updated_at,
              }}
              limit={isProposal ? 12 : 8}
            />
          </div>
        </details>
      ) : null}

      {actionable ? (
        <div className="mt-4 grid gap-2">
          <p className="text-xs leading-5 text-muted-foreground">
            {t(
              language,
              canRunNow ? "review.runNow.detail"
                : isProposal ? "review.proposal.detail"
                : item.source === "agent_followup" ? "review.agent.detail"
                : "review.chat.detail",
            )}
          </p>
          {isProposal ? (
            <Input
              value={rejectReason}
              onChange={(event) => setRejectReason(event.target.value)}
              placeholder={t(language, "review.proposal.rejectReason")}
              aria-label={t(language, "review.proposal.rejectReason")}
            />
          ) : null}
          <div className="flex flex-wrap gap-2" aria-label={t(language, "review.actions.aria")}>
            {canRunNow ? (
              <ActionButton
                label={t(language, "review.runNow")}
                successLabel={hadRun ? t(language, "review.regenerated") : t(language, "review.executed")}
                action={() => onAction(item, "run_now", hadRun)}
                invalidate={[]}
              />
            ) : null}
            <ActionButton
              label={t(language, isProposal ? "proposals.approve" : "review.approve")}
              action={() => onAction(item, "approve")}
              invalidate={[]}
            />
            {!snoozed ? <ActionButton label={t(language, "review.snoozeDay")} action={() => onAction(item, "snooze")} invalidate={[]} /> : null}
            <ActionButton
              label={t(language, isProposal ? "proposals.reject" : "review.dismiss")}
              action={() => onAction(item, "dismiss", false, isProposal ? rejectReason : undefined)}
              invalidate={[]}
              variant="destructive"
            />
          </div>
        </div>
      ) : null}
      {feedback?.conflict && isProposal ? (
        <ProposalConflictNote language={language} itemId={item.id} />
      ) : feedback ? (
        <p className={`mt-2 text-xs ${feedback.tone === "error" ? "text-warning" : "text-success"}`}>
          {feedback.message} - {t(language, "review.feedback.open")}
          {feedback.detail && feedback.detail !== feedback.message ? (
            <span className="mt-0.5 block text-[11px] opacity-75">{feedback.detail}</span>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
