import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitMerge, RotateCcw, RefreshCw } from "lucide-react";
import { brainGovernanceApi, type EntityMergeCandidate } from "@/api/brainGovernance";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { t, type Language } from "@/i18n";

export function EntityMergePanel({ language }: { language: Language }) {
  const queryClient = useQueryClient();
  const candidatesQuery = useQuery({
    queryKey: ["brain-entity-candidates"],
    queryFn: () => brainGovernanceApi.entityCandidates(),
  });

  const mergeMutation = useMutation({
    mutationFn: (cand: EntityMergeCandidate) =>
      brainGovernanceApi.entityMerge({
        canonical_id: cand.canonical_id,
        duplicate_id: cand.duplicate_id,
        rationale: cand.rationale,
        signals: cand.signals as unknown as Record<string, unknown>,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["brain-entity-candidates"] });
    },
  });

  const rollbackMutation = useMutation({
    mutationFn: (mergeId: string) =>
      brainGovernanceApi.entityRollback({
        merge_id: mergeId,
        add_do_not_merge: true,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["brain-entity-candidates"] });
    },
  });

  const candidates = candidatesQuery.data?.data?.candidates ?? [];
  const recentMerges = candidatesQuery.data?.data?.recent_merges ?? [];

  return (
    <Card data-testid="entity-merge-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <GitMerge className="h-4 w-4" aria-hidden="true" />
          {t(language, "brain.gov.entities.title")}
        </CardTitle>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => void candidatesQuery.refetch()}
        >
          <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
          {t(language, "brain.gov.refresh")}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t(language, "brain.gov.entities.candidates")} ({candidates.length})
          </h4>
          {candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t(language, "brain.gov.entities.noCandidates")}
            </p>
          ) : (
            <ul className="space-y-2">
              {candidates.map((cand) => (
                <li
                  key={`${cand.canonical_id}-${cand.duplicate_id}`}
                  className="rounded-md border p-3 text-sm space-y-1.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">
                      {cand.canonical_title} ← {cand.duplicate_title}
                    </span>
                    <div className="flex items-center gap-1.5">
                      <Badge variant="muted">{cand.decision}</Badge>
                      <Badge variant="default">
                        {t(language, "brain.gov.entities.score")}:{" "}
                        {Math.round(cand.signals.combined_score * 100)}%
                      </Badge>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">{cand.rationale}</p>
                  <div>
                    <Button
                      type="button"
                      size="sm"
                      data-testid={`merge-candidate-${cand.duplicate_id}`}
                      disabled={mergeMutation.isPending}
                      onClick={() => mergeMutation.mutate(cand)}
                    >
                      {t(language, "brain.gov.entities.merge")}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t(language, "brain.gov.entities.recentMerges")} ({recentMerges.length})
          </h4>
          {recentMerges.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t(language, "brain.gov.entities.noMerges")}
            </p>
          ) : (
            <ul className="space-y-2">
              {recentMerges.map((rec) => (
                <li
                  key={rec.merge_id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm"
                >
                  <div>
                    <div className="font-medium">
                      {rec.canonical_id} ← {rec.duplicate_id}
                    </div>
                    <div className="text-xs text-muted-foreground">{rec.rationale}</div>
                  </div>
                  {rec.rolled_back_at ? (
                    <Badge variant="muted">
                      {t(language, "brain.gov.entities.rolledBack")}
                    </Badge>
                  ) : (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      data-testid={`rollback-merge-${rec.merge_id}`}
                      disabled={rollbackMutation.isPending}
                      onClick={() => rollbackMutation.mutate(rec.merge_id)}
                    >
                      <RotateCcw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                      {t(language, "brain.gov.entities.rollback")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
