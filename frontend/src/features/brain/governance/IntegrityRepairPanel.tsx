import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, ShieldAlert, Wrench, RefreshCw } from "lucide-react";
import { brainGovernanceApi, type RepairExecutionResult } from "@/api/brainGovernance";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { t, type Language } from "@/i18n";

export function IntegrityRepairPanel({ language }: { language: Language }) {
  const queryClient = useQueryClient();
  const [approved, setApproved] = React.useState<string[]>([]);
  const [preview, setPreview] = React.useState<RepairExecutionResult | null>(null);

  const reportQuery = useQuery({
    queryKey: ["brain-integrity"],
    queryFn: () => brainGovernanceApi.integrityCheck(false),
  });

  const repairMutation = useMutation({
    mutationFn: (dryRun: boolean) =>
      brainGovernanceApi.integrityRepair({
        dry_run: dryRun,
        approved_actions: approved,
        global: false,
      }),
    onSuccess: (res) => {
      if (res.ok && res.data) {
        setPreview(res.data);
        if (!res.data.dry_run) {
          void queryClient.invalidateQueries({ queryKey: ["brain-integrity"] });
        }
      }
    },
  });

  const report = reportQuery.data?.data;
  const toggleAction = (actionId: string) => {
    setApproved((prev) =>
      prev.includes(actionId) ? prev.filter((id) => id !== actionId) : [...prev, actionId],
    );
  };

  return (
    <Card data-testid="integrity-repair-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-base">
          {report?.healthy ? (
            <ShieldCheck className="h-4 w-4 text-emerald-500" aria-hidden="true" />
          ) : (
            <ShieldAlert className="h-4 w-4 text-amber-500" aria-hidden="true" />
          )}
          {t(language, "brain.gov.integrity.title")}
        </CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant={report?.healthy ? "success" : "danger"}>
            {report?.healthy
              ? t(language, "brain.gov.integrity.healthy")
              : `${t(language, "brain.gov.integrity.issues")}: ${report?.total_issues ?? 0}`}
          </Badge>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void reportQuery.refetch()}
          >
            <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "brain.gov.refresh")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="integrity-dry-run-btn"
            disabled={repairMutation.isPending}
            onClick={() => repairMutation.mutate(true)}
          >
            {t(language, "brain.gov.integrity.dryRun")}
          </Button>
          <Button
            type="button"
            size="sm"
            data-testid="integrity-execute-btn"
            disabled={repairMutation.isPending}
            onClick={() => repairMutation.mutate(false)}
          >
            <Wrench className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "brain.gov.integrity.execute")}
          </Button>
        </div>

        {report && report.findings.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t(language, "brain.gov.integrity.noFindings")}
          </p>
        ) : (
          <ul className="space-y-2">
            {(report?.findings ?? []).map((finding) => (
              <li
                key={`${finding.kind}-${finding.detail}`}
                className="rounded-md border p-3 text-sm"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{finding.kind}</span>
                  <div className="flex items-center gap-1.5">
                    <Badge variant={finding.severity === "critical" ? "danger" : "warning"}>
                      {finding.severity} ({finding.count})
                    </Badge>
                    <Badge variant="muted">{finding.safety}</Badge>
                  </div>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{finding.detail}</p>
              </li>
            ))}
          </ul>
        )}

        {preview?.plan ? (
          <div
            className="rounded-md border bg-muted/30 p-3 text-xs space-y-2"
            data-testid="integrity-blast-radius"
          >
            <div className="font-semibold">{t(language, "brain.gov.integrity.blastRadius")}</div>
            <div className="flex flex-wrap gap-4">
              <span>
                {t(language, "brain.gov.integrity.rowsDeleted")}:{" "}
                <strong>{preview.plan.blast_radius.rows_deleted}</strong>
              </span>
              <span>
                {t(language, "brain.gov.integrity.rowsUpdated")}:{" "}
                <strong>{preview.plan.blast_radius.rows_updated}</strong>
              </span>
              <span>
                {t(language, "brain.gov.integrity.repaired")}:{" "}
                <strong>{preview.rows_repaired}</strong>
              </span>
            </div>
            {preview.plan.blast_radius.tables_touched.length > 0 ? (
              <div>
                {t(language, "brain.gov.integrity.tables")}:{" "}
                {preview.plan.blast_radius.tables_touched.join(", ")}
              </div>
            ) : null}
            {preview.plan.actions
              .filter((a) => a.safety === "requires_approval")
              .map((action) => (
                <label key={action.action_id} className="flex items-center gap-2 pt-1">
                  <input
                    type="checkbox"
                    checked={approved.includes(action.action_id)}
                    onChange={() => toggleAction(action.action_id)}
                  />
                  <span>
                    {t(language, "brain.gov.integrity.approveAction")}: {action.action_id} (
                    {action.estimated_rows})
                  </span>
                </label>
              ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
