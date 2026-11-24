import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, ArrowRightLeft, RotateCcw, RefreshCw } from "lucide-react";
import { brainGovernanceApi } from "@/api/brainGovernance";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { t, type Language } from "@/i18n";

export function EmbeddingRegistryPanel({ language }: { language: Language }) {
  const queryClient = useQueryClient();
  const registryQuery = useQuery({
    queryKey: ["brain-embedding-registry"],
    queryFn: () => brainGovernanceApi.embeddingRegistry(),
  });

  const cutoverMutation = useMutation({
    mutationFn: (generationId: string) =>
      brainGovernanceApi.embeddingCutover({ generation_id: generationId }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["brain-embedding-registry"] });
    },
  });

  const rollbackMutation = useMutation({
    mutationFn: () => brainGovernanceApi.embeddingRollback(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["brain-embedding-registry"] });
    },
  });

  const models = registryQuery.data?.data?.models ?? [];
  const generations = registryQuery.data?.data?.generations ?? [];

  return (
    <Card data-testid="embedding-registry-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Cpu className="h-4 w-4" aria-hidden="true" />
          {t(language, "brain.gov.embeddings.title")}
        </CardTitle>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="embedding-rollback-btn"
            disabled={rollbackMutation.isPending}
            onClick={() => rollbackMutation.mutate()}
          >
            <RotateCcw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "brain.gov.embeddings.rollback")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void registryQuery.refetch()}
          >
            <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "brain.gov.refresh")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t(language, "brain.gov.embeddings.models")} ({models.length})
          </h4>
          <ul className="space-y-2">
            {models.map((m) => (
              <li
                key={`${m.model_id}@${m.version}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm"
              >
                <div>
                  <div className="font-medium">
                    {m.model_id}{" "}
                    <span className="text-xs text-muted-foreground">{m.version}</span>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {[m.provider, String(m.dimensions), m.normalization, m.distance_metric].join(" / ")}
                  </div>
                </div>
                <Badge variant={m.status === "active" ? "success" : "muted"}>{m.status}</Badge>
              </li>
            ))}
          </ul>
        </section>

        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t(language, "brain.gov.embeddings.generations")} ({generations.length})
          </h4>
          {generations.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t(language, "brain.gov.embeddings.noGenerations")}
            </p>
          ) : (
            <ul className="space-y-2">
              {generations.map((gen) => {
                const pct =
                  gen.total_items > 0
                    ? Math.min(100, Math.round((gen.processed_items / gen.total_items) * 100))
                    : 0;
                return (
                  <li key={gen.generation_id} className="rounded-md border p-3 text-sm space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium">
                        {gen.generation_id} ({gen.model_id})
                      </span>
                      <Badge variant="muted">{gen.status}</Badge>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {t(language, "brain.gov.embeddings.progress")}: {gen.processed_items}/
                      {gen.total_items} ({pct}%)
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded bg-muted">
                      <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
                    </div>
                    {gen.shadow_recall_at_5 != null && gen.active_recall_at_5 != null ? (
                      <div className="text-xs">
                        {`${t(language, "brain.gov.embeddings.shadowEval")}: ${(gen.shadow_recall_at_5 * 100).toFixed(1)}% / ${(gen.active_recall_at_5 * 100).toFixed(1)}%`}
                      </div>
                    ) : null}
                    {gen.status !== "active" && gen.status !== "evaluated_fail" ? (
                      <Button
                        type="button"
                        size="sm"
                        data-testid={`cutover-gen-${gen.generation_id}`}
                        disabled={cutoverMutation.isPending}
                        onClick={() => cutoverMutation.mutate(gen.generation_id)}
                      >
                        <ArrowRightLeft className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                        {t(language, "brain.gov.embeddings.cutover")}
                      </Button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
