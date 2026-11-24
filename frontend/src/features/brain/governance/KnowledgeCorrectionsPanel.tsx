import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileEdit, RotateCcw, Shield, XCircle } from "lucide-react";
import { brainGovernanceApi } from "@/api/brainGovernance";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";

export function KnowledgeCorrectionsPanel({ language }: { language: Language }) {
  const queryClient = useQueryClient();
  const [targetKind, setTargetKind] = React.useState("fact");
  const [targetId, setTargetId] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [afterSummary, setAfterSummary] = React.useState("");

  const listQuery = useQuery({
    queryKey: ["brain-corrections"],
    queryFn: () => brainGovernanceApi.listCorrections(),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      brainGovernanceApi.createCorrection({
        target_kind: targetKind,
        target_id: targetId.trim(),
        status: "pending",
        reason: reason.trim(),
        before_snapshot: {},
        after_snapshot: afterSummary.trim() ? { summary: afterSummary.trim() } : {},
      }),
    onSuccess: (res) => {
      if (res.ok) {
        setTargetId("");
        setReason("");
        setAfterSummary("");
        void queryClient.invalidateQueries({ queryKey: ["brain-corrections"] });
      }
    },
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      brainGovernanceApi.updateCorrectionStatus(id, { status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["brain-corrections"] });
    },
  });

  const revertMutation = useMutation({
    mutationFn: (id: string) => brainGovernanceApi.revertCorrection(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["brain-corrections"] });
    },
  });

  const corrections = listQuery.data?.data?.corrections ?? [];

  return (
    <Card data-testid="knowledge-corrections-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <FileEdit className="h-4 w-4" aria-hidden="true" />
          {t(language, "brain.gov.corrections.title")}
        </CardTitle>
        <Badge variant="muted" className="flex items-center gap-1">
          <Shield className="h-3 w-3" aria-hidden="true" />
          {t(language, "brain.gov.corrections.localOnly")}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          className="grid gap-2 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (targetId.trim()) createMutation.mutate();
          }}
        >
          <select
            aria-label={t(language, "brain.gov.corrections.targetKind")}
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={targetKind}
            onChange={(e) => setTargetKind(e.target.value)}
          >
            <option value="fact">{t(language, "brain.gov.corrections.kind.fact")}</option>
            <option value="relationship">
              {t(language, "brain.gov.corrections.kind.relationship")}
            </option>
            <option value="citation">{t(language, "brain.gov.corrections.kind.citation")}</option>
            <option value="entity_link">
              {t(language, "brain.gov.corrections.kind.entity_link")}
            </option>
          </select>
          <Input
            data-testid="correction-target-id"
            placeholder={t(language, "brain.gov.corrections.targetId")}
            value={targetId}
            onChange={(e) => setTargetId(e.target.value)}
          />
          <Input
            data-testid="correction-reason"
            placeholder={t(language, "brain.gov.corrections.reason")}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <Input
            data-testid="correction-after-summary"
            placeholder={t(language, "brain.gov.corrections.afterSummary")}
            value={afterSummary}
            onChange={(e) => setAfterSummary(e.target.value)}
          />
          <div className="sm:col-span-2">
            <Button
              type="submit"
              size="sm"
              data-testid="correction-submit-btn"
              disabled={!targetId.trim() || createMutation.isPending}
            >
              {t(language, "brain.gov.corrections.submit")}
            </Button>
          </div>
        </form>

        {corrections.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t(language, "brain.gov.corrections.empty")}
          </p>
        ) : (
          <ul className="space-y-2">
            {corrections.map((item) => (
              <li key={item.correction_id} className="rounded-md border p-3 text-sm space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">
                    [{item.target_kind}] {item.target_id}
                  </span>
                  <Badge variant="muted">{item.status}</Badge>
                </div>
                {item.reason ? (
                  <p className="text-xs text-muted-foreground">{item.reason}</p>
                ) : null}
                <div className="grid gap-2 text-xs sm:grid-cols-2">
                  <div className="rounded bg-muted/40 p-2">
                    <span className="font-semibold">
                      {t(language, "brain.gov.corrections.before")}:
                    </span>{" "}
                    <code>{JSON.stringify(item.before_snapshot)}</code>
                  </div>
                  <div className="rounded bg-muted/40 p-2">
                    <span className="font-semibold">
                      {t(language, "brain.gov.corrections.after")}:
                    </span>{" "}
                    <code>{JSON.stringify(item.after_snapshot)}</code>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {item.status === "pending" ? (
                    <>
                      <Button
                        type="button"
                        size="sm"
                        data-testid={`confirm-correction-${item.correction_id}`}
                        onClick={() =>
                          statusMutation.mutate({ id: item.correction_id, status: "confirmed" })
                        }
                      >
                        <CheckCircle2 className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                        {t(language, "brain.gov.corrections.confirm")}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          statusMutation.mutate({ id: item.correction_id, status: "rejected" })
                        }
                      >
                        <XCircle className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                        {t(language, "brain.gov.corrections.reject")}
                      </Button>
                    </>
                  ) : null}
                  {item.status === "confirmed" && !item.reverted_at ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      data-testid={`revert-correction-${item.correction_id}`}
                      onClick={() => revertMutation.mutate(item.correction_id)}
                    >
                      <RotateCcw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                      {t(language, "brain.gov.corrections.revert")}
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
