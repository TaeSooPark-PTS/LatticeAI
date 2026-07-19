import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ChevronDown, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { Button } from "@/components/ui/button";
import { t, type Language } from "@/i18n";
import { isRecord } from "./brainData";

const DIMENSION_KEYS = ["freshness", "connectivity", "embedding_coverage", "consistency"] as const;
const ACTION_KEYS = new Set([
  "rebuild_vector_index",
  "review_orphans",
  "refresh_stale_knowledge",
  "resolve_contradictions",
]);

export function BrainIntelligencePanel({ language }: { language: Language }) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = React.useState(false);
  const healthQ = useQuery({ queryKey: ["brainHealth"], queryFn: latticeApi.brainHealth, enabled: expanded });
  const insightsQ = useQuery({ queryKey: ["brainInsights"], queryFn: latticeApi.brainInsights, enabled: expanded });
  const contradictionsQ = useQuery({
    queryKey: ["brainContradictions"],
    queryFn: latticeApi.brainContradictions,
    enabled: expanded,
  });
  const [consolidation, setConsolidation] = React.useState<Record<string, unknown> | null>(null);
  const consolidatePreview = useMutation({
    mutationFn: () => latticeApi.brainConsolidate(false),
    onSuccess: (result) => {
      if (result.ok && isRecord(result.data)) setConsolidation(result.data);
    },
  });
  const consolidateApply = useMutation({
    mutationFn: () => latticeApi.brainConsolidate(true),
    onSuccess: (result) => {
      if (result.ok && isRecord(result.data)) setConsolidation(result.data);
      void qc.invalidateQueries({ queryKey: ["brainHealth"] });
    },
  });

  const health = healthQ.data?.ok && isRecord(healthQ.data.data) ? healthQ.data.data : null;
  const healthUnavailable = expanded && !healthQ.isPending && (!healthQ.data?.ok || healthQ.data?.source === "unavailable");
  const insights = insightsQ.data?.ok && isRecord(insightsQ.data.data) ? insightsQ.data.data : null;
  const contradictionCount =
    contradictionsQ.data?.ok && isRecord(contradictionsQ.data.data)
      ? Number(contradictionsQ.data.data.count || 0)
      : 0;

  const overallScore = health && typeof health.overall_score === "number" ? health.overall_score : null;
  const grade = health && typeof health.grade === "string" ? health.grade : null;
  const dimensions = health && isRecord(health.dimensions) ? health.dimensions : {};
  const actions = health && Array.isArray(health.recommended_actions) ? health.recommended_actions : [];
  const activity = insights && isRecord(insights.activity) ? insights.activity : {};
  const attention = insights && isRecord(insights.attention) ? insights.attention : {};

  return (
    <section
      className={`brain-care-panel brain-intelligence-panel ${expanded ? "is-expanded" : "is-collapsed"}`}
      aria-label={t(language, "intelligence.title")}
    >
      <button
        className="brain-care-summary"
        type="button"
        aria-expanded={expanded}
        aria-controls="brain-intelligence-details"
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="brain-care-summary-main">
          <span><Sparkles className="h-3.5 w-3.5" /> {t(language, "intelligence.title")}</span>
          <strong>{t(language, "intelligence.subtitle")}</strong>
        </span>
        {expanded && overallScore !== null ? (
          <div className="brain-care-proof">
            <span>{t(language, "intelligence.score")}: {overallScore}</span>
            <span>{gradeLabel(grade, language)}</span>
          </div>
        ) : null}
        <ChevronDown className="brain-care-toggle h-4 w-4" aria-hidden="true" />
      </button>

      {expanded ? (
        <div id="brain-intelligence-details" className="brain-care-details">
          {healthQ.isPending ? (
            <p className="brain-care-note">{t(language, "intelligence.loading")}</p>
          ) : healthUnavailable ? (
            <p className="brain-care-note">{t(language, "intelligence.unavailable")}</p>
          ) : (
            <>
              <div className="brain-intelligence-dimensions" role="list">
                {DIMENSION_KEYS.map((key) => {
                  const dim = isRecord(dimensions[key]) ? (dimensions[key] as Record<string, unknown>) : {};
                  const score = typeof dim.score === "number" ? dim.score : null;
                  return (
                    <div className="brain-intelligence-dimension" role="listitem" key={key}>
                      <small>{t(language, `intelligence.dim.${key}`)}</small>
                      <strong>{score === null ? "—" : score}</strong>
                    </div>
                  );
                })}
              </div>

              <div className="brain-intelligence-insights" role="status">
                <span>
                  <Activity className="h-3.5 w-3.5" aria-hidden="true" />{" "}
                  {t(language, "intelligence.insights.recent", {
                    count: String(numberValue(activity.recent_nodes)),
                  })}
                </span>
                <span>{t(language, "intelligence.insights.stale", { count: String(numberValue(attention.stale_nodes)) })}</span>
                <span>{t(language, "intelligence.insights.orphans", { count: String(numberValue(attention.orphan_nodes)) })}</span>
                <span>{t(language, "intelligence.contradictions", { count: String(contradictionCount) })}</span>
              </div>

              {actions.length ? (
                <div className="brain-intelligence-actions">
                  <small>{t(language, "intelligence.actions")}</small>
                  <ul>
                    {actions.map((action, index) => {
                      const record: Record<string, unknown> = isRecord(action) ? action : {};
                      const id = String(record.id || "");
                      return (
                        <li key={`${id}-${index}`}>
                          {ACTION_KEYS.has(id) ? t(language, `intelligence.action.${id}`) : String(record.reason || id)}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : null}

              <div className="brain-care-archive-actions">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={consolidatePreview.isPending}
                  onClick={() => consolidatePreview.mutate()}
                >
                  {consolidatePreview.isPending
                    ? t(language, "intelligence.consolidate.working")
                    : t(language, "intelligence.consolidate")}
                </Button>
                {consolidation && numberValue(consolidation.duplicate_memory_count) > 0 && consolidation.mode !== "applied" ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={consolidateApply.isPending}
                    onClick={() => consolidateApply.mutate()}
                  >
                    {t(language, "intelligence.consolidate.apply")}
                  </Button>
                ) : null}
              </div>
              {consolidation ? (
                <p className="brain-care-note" role="status">
                  {consolidation.mode === "applied"
                    ? t(language, "intelligence.consolidate.applied", {
                        count: String(numberValue(consolidation.pruned)),
                      })
                    : numberValue(consolidation.duplicate_memory_count) > 0 ||
                        numberValue(consolidation.duplicate_edge_count) > 0
                      ? t(language, "intelligence.consolidate.found", {
                          memories: String(numberValue(consolidation.duplicate_memory_count)),
                          edges: String(numberValue(consolidation.duplicate_edge_count)),
                        })
                      : t(language, "intelligence.consolidate.none")}
                </p>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}

function gradeLabel(grade: string | null, language: Language) {
  const known = new Set(["excellent", "good", "attention", "critical"]);
  return t(language, `intelligence.grade.${grade && known.has(grade) ? grade : "unknown"}`);
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
