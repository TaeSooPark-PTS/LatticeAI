import * as React from "react";
import "@/i18n/workspace";

import { latticeApi, type ReviewItem } from "@/api/client";
import { BULK_REVIEW_CAP, type BulkReviewAction, type BulkReviewResult } from "@/api/actReview";
import { Button } from "@/components/ui/button";
import { t, type Language } from "@/i18n";

export function isBulkEligible(item: ReviewItem): boolean {
  return item.source !== "change_proposal" && (item.effective_status === "pending" || item.effective_status === "snoozed");
}

type Outcome = { error: string } | (BulkReviewResult & { lines: Array<{ id: string; text: string }> });

export function BulkReviewBar({
  language,
  items,
  selected,
  onSelectedChange,
  onDone,
}: {
  language: Language;
  items: ReviewItem[];
  selected: string[];
  onSelectedChange: (ids: string[]) => void;
  onDone: () => Promise<unknown>;
}) {
  const [confirming, setConfirming] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [outcome, setOutcome] = React.useState<Outcome | null>(null);
  const confirmRef = React.useRef<HTMLButtonElement>(null);
  const eligible = items.filter(isBulkEligible).slice(0, BULK_REVIEW_CAP).map((item) => item.id);
  const proposals = items.some((item) => item.source === "change_proposal");
  const count = selected.length;

  React.useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);
  React.useEffect(() => {
    if (!count) setConfirming(false);
  }, [count]);

  const run = async (action: BulkReviewAction) => {
    setConfirming(false);
    setBusy(true);
    setOutcome(null);
    const result = await latticeApi.bulkReview(action, selected);
    if (!result.ok) {
      setOutcome({ error: result.error || t(language, "review.feedback.error") });
    } else {
      const failed = result.data.results.filter((row) => row.status !== "ok");
      setOutcome({
        ...result.data,
        lines: failed.map((row) => ({
          id: row.id,
          text: `${items.find((item) => item.id === row.id)?.title || row.id}: ${row.detail || t(language, `review.bulk.status.${row.status}`)}`,
        })),
      });
      onSelectedChange(failed.map((row) => row.id));
      await onDone();
    }
    setBusy(false);
  };

  return (
    <div className="space-y-2">
      {eligible.length ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-border p-2.5">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={count === eligible.length}
              onChange={(event) => onSelectedChange(event.target.checked ? eligible : [])}
            />
            {t(language, "review.bulk.selectAll")}
          </label>
          <span role="status" className="text-sm tabular-nums text-muted-foreground">
            {t(language, "review.bulk.selected", { count })}
          </span>
          {confirming ? (
            <div role="group" aria-label={t(language, "review.bulk.confirmAria")} className="ml-auto flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{t(language, "review.bulk.confirm", { count })}</span>
              <Button ref={confirmRef} size="sm" onClick={() => void run("approve")}>{t(language, "review.bulk.confirmYes")}</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>{t(language, "review.bulk.cancel")}</Button>
            </div>
          ) : (
            <div className="ml-auto flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={!count || busy} onClick={() => setConfirming(true)}>{t(language, "review.bulk.approve")}</Button>
              <Button size="sm" variant="outline" disabled={!count || busy} onClick={() => void run("dismiss")}>{t(language, "review.bulk.dismiss")}</Button>
            </div>
          )}
        </div>
      ) : null}
      {proposals && eligible.length ? <p className="text-xs text-muted-foreground">{t(language, "review.bulk.proposalsExcluded")}</p> : null}
      {outcome && "error" in outcome ? <p role="alert" className="text-sm text-destructive">{outcome.error}</p> : null}
      {outcome && !("error" in outcome) ? (
        <div role={outcome.failed ? "alert" : "status"} className="space-y-1 text-sm">
          <p className="tabular-nums">{t(language, "review.bulk.result", { succeeded: outcome.succeeded, failed: outcome.failed })}</p>
          {outcome.lines.length ? (
            <ul className="m-0 pl-5 text-xs text-destructive">
              {outcome.lines.map((line) => <li key={line.id}>{line.text}</li>)}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
