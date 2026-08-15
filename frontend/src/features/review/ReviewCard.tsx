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
  // The card names itself by its own title. `item.id` is server-issued and can
  // carry characters that are not valid in an IDREF, so the id comes from React
  // rather than from the item.
  const titleId = React.useId();
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
  const showTechnical = mode !== "basic";
  // The two-column split only earns its keep when the evidence side has
  // something in it. A chat follow-up in basic mode carries no diff, no risk
  // badges and no technical panel, and would otherwise render a 7-column void
  // beside the decision panel. Those cards stay single-column.
  const hasEvidence = Boolean(
    (isProposal && diff.length > 0) || snoozed || riskLabel || changeLabel || toolName || proposedBy || showTechnical,
  );

  return (
    // <article>, not <div>. A bare <header> whose nearest sectioning ancestor is
    // a plain div maps to the `banner` landmark, so an inbox of N cards was
    // announcing N page banners. Wrapping the card scopes the header to it and
    // makes each item one navigable unit.
    <article aria-labelledby={titleId} className="review-card">
      {/* Header Bar */}
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border/40 pb-3">
        <div className="min-w-0 flex-1 space-y-1">
          {/* The item title is the card's heading. It used to be a plain div
              while "결정하기" below was the <h3>, so heading navigation listed
              N identical "결정하기" entries and never the items themselves. */}
          <h3 id={titleId} className="font-bold text-base text-foreground">{item.title}</h3>
          {item.summary ? <p className="text-xs leading-relaxed text-muted-foreground">{item.summary}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-1.5 shrink-0">
          <Badge variant="muted" className="text-xs">{reviewSourceLabel(language, item.source)}</Badge>
          {isProposal ? (
            <Badge variant="muted" className="text-xs">
              {t(language, item.kind === "file_delete"
                ? "proposals.kind.delete"
                : proposalTier === "large" ? "proposals.tier.large" : "proposals.tier.small")}
            </Badge>
          ) : null}
          <Badge variant={reviewStatusVariant(item.effective_status)} className="text-xs font-semibold">
            {reviewStatusLabel(language, item.effective_status)}
          </Badge>
        </div>
      </header>

      {/* Two-column body: evidence on the left, the decision on the right. This is
          a plain div — a card repeated N times in a list must not emit <main>. */}
      <div className={`grid items-start gap-5 ${hasEvidence ? "md:grid-cols-12" : ""}`}>
        {/* Left column: the evidence — what actually changes, and how risky it is. */}
        <div className={hasEvidence ? "md:col-span-7 space-y-3" : "hidden"}>
          {isProposal && diff.length > 0 ? (
            <div className="rounded-lg border border-border/60 bg-muted/20 p-3 max-h-64 overflow-y-auto">
              <ProposalDiff language={language} diff={diff} path={diffPath} />
            </div>
          ) : null}

          {snoozed ? (
            <div className="review-card-snooze">
              <div>
                <div className="review-card-snooze-title">{formatSnoozedUntil(language, item.snoozed_until)}</div>
                <p className="text-muted-foreground text-[11px]">{t(language, "review.snoozed.detail")}</p>
              </div>
              <Button size="sm" variant="outline" onClick={() => onAction(item, "unsnooze")} disabled={!actionable} className="h-7 text-xs">
                <RotateCcw className="h-3 w-3 mr-1" /> {t(language, "review.unsnooze")}
              </Button>
            </div>
          ) : null}

          {riskLabel || changeLabel || toolName || proposedBy ? (
            <div className="rounded-lg border border-border/40 bg-muted/10 p-3 space-y-2 text-xs">
              {changeLabel || riskLabel ? (
                <div className="flex flex-wrap items-center gap-2">
                  {changeLabel ? <Badge variant="muted" className="text-[10px]">{changeLabel}</Badge> : null}
                  {riskLabel ? <Badge variant="muted" className="text-[10px]">{riskLabel}</Badge> : null}
                </div>
              ) : null}
              {toolName ? (
                <p className="m-0 text-muted-foreground">
                  <span className="font-semibold text-foreground">{t(language, "review.summary.tool")}</span> · {toolName}
                </p>
              ) : null}
              {proposedBy ? (
                <p className="m-0 text-muted-foreground">
                  <span className="font-semibold text-foreground">{t(language, "review.summary.proposedBy")}</span> · {proposedBy}
                </p>
              ) : null}
            </div>
          ) : null}

          {showTechnical ? (
            <details className="rounded-lg border border-border/50 text-xs">
              <summary className="cursor-pointer select-none p-2.5 font-medium text-muted-foreground hover:text-foreground">
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
        </div>

        {/* Right column: the decision. Approve or reject sit together, always
            in the same place, so the eye lands on them without hunting. */}
        <div className={`rounded-xl border border-border/80 bg-muted/20 p-4 space-y-3 ${hasEvidence ? "md:col-span-5" : ""}`}>
          {/* h4, one level under the item title above it. Repeated per card,
              but now nested beneath a unique heading rather than replacing it. */}
          <h4 className="text-xs font-bold tracking-wide text-muted-foreground">
            {t(language, "review.decision.title")}
          </h4>

          {actionable ? (
            <div className="space-y-3">
              <p className="text-xs leading-relaxed text-muted-foreground">
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
                  className="text-xs h-8"
                />
              ) : null}
              <div className="flex flex-col gap-2 pt-1" role="group" aria-label={t(language, "review.actions.aria")}>
                <div className="flex gap-2">
                  <ActionButton
                    label={t(language, isProposal ? "proposals.approve" : "review.approve")}
                    action={() => onAction(item, "approve")}
                    invalidate={[]}
                  />
                  <ActionButton
                    label={t(language, isProposal ? "proposals.reject" : "review.dismiss")}
                    action={() => onAction(item, "dismiss", false, isProposal ? rejectReason : undefined)}
                    invalidate={[]}
                    variant="destructive"
                  />
                </div>
                <div className="flex gap-2">
                  {canRunNow ? (
                    <ActionButton
                      label={t(language, "review.runNow")}
                      successLabel={hadRun ? t(language, "review.regenerated") : t(language, "review.executed")}
                      action={() => onAction(item, "run_now", hadRun)}
                      invalidate={[]}
                    />
                  ) : null}
                  {!snoozed ? <ActionButton label={t(language, "review.snoozeDay")} action={() => onAction(item, "snooze")} invalidate={[]} /> : null}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground italic">
              {reviewStatusLabel(language, item.effective_status)}
            </p>
          )}

          {feedback?.conflict && isProposal ? (
            <ProposalConflictNote language={language} itemId={item.id} />
          ) : feedback ? (
            <p className={`review-card-feedback ${feedback.tone === "error" ? "is-error" : "is-ok"}`}>
              {feedback.message} - {t(language, "review.feedback.open")}
              {feedback.detail && feedback.detail !== feedback.message ? (
                <span className="mt-0.5 block text-[11px] opacity-75">{feedback.detail}</span>
              ) : null}
            </p>
          ) : null}
        </div>
      </div>
    </article>
  );
}
