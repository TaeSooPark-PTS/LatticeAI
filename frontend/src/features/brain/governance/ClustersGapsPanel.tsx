import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Network, AlertTriangle, RefreshCw } from "lucide-react";
import { brainGovernanceApi } from "@/api/brainGovernance";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { t, type Language } from "@/i18n";

export function ClustersGapsPanel({ language }: { language: Language }) {
  const clustersQuery = useQuery({
    queryKey: ["brain-topic-clusters"],
    queryFn: () => brainGovernanceApi.topicClusters(),
  });
  const gapsQuery = useQuery({
    queryKey: ["brain-knowledge-gaps"],
    queryFn: () => brainGovernanceApi.knowledgeGaps(),
  });

  const clusters = clustersQuery.data?.data?.clusters ?? [];
  const gaps = gapsQuery.data?.data;

  return (
    <Card data-testid="clusters-gaps-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Network className="h-4 w-4" aria-hidden="true" />
          {t(language, "brain.gov.clusters.title")}
        </CardTitle>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => {
            void clustersQuery.refetch();
            void gapsQuery.refetch();
          }}
        >
          <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
          {t(language, "brain.gov.refresh")}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t(language, "brain.gov.clusters.topics")} ({clusters.length})
          </h4>
          {clusters.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t(language, "brain.gov.clusters.noClusters")}
            </p>
          ) : (
            <ul className="space-y-2">
              {clusters.map((cluster) => (
                <li key={cluster.cluster_id} className="rounded-md border p-3 text-sm space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{cluster.cluster_id}</span>
                    <Badge variant="muted">{cluster.size}</Badge>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {cluster.members.map((m) => (
                      <Badge key={m.id} variant="default">
                        {m.title} ({m.type})
                      </Badge>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="space-y-2">
          <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "brain.gov.clusters.gaps")}
          </h4>
          {!gaps || (gaps.isolated_entities_count === 0 && gaps.missing_provenance_count === 0) ? (
            <p className="text-sm text-muted-foreground">
              {t(language, "brain.gov.clusters.noGaps")}
            </p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-md border p-3 text-sm space-y-1.5">
                <div className="font-medium">
                  {t(language, "brain.gov.clusters.isolated")} ({gaps.isolated_entities_count})
                </div>
                <ul className="space-y-1 text-xs text-muted-foreground">
                  {gaps.isolated_entities.map((e) => (
                    <li key={e.id}>
                      {e.title} ({e.type})
                    </li>
                  ))}
                </ul>
              </div>
              <div className="rounded-md border p-3 text-sm space-y-1.5">
                <div className="font-medium">
                  {t(language, "brain.gov.clusters.missingProv")} ({gaps.missing_provenance_count})
                </div>
                <ul className="space-y-1 text-xs text-muted-foreground">
                  {gaps.missing_provenance.map((e) => (
                    <li key={e.id}>
                      {e.title} ({e.type})
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
