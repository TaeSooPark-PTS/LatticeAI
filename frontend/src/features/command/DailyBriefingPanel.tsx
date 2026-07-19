import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronDown, Sunrise } from "lucide-react";
import { latticeApi } from "@/api/client";
import { asArray } from "@/lib/utils";
import { t, type Language } from "@/i18n";
import { navigateHash } from "@/features/brain/navigation";

type QuickAction = { id: string; kind: string; count: number; target: string };

const ACTION_LABEL_KEYS: Record<string, string> = {
  "review-pending": "briefing.action.reviewPending",
  "enable-drafts": "briefing.action.enableDrafts",
  "install-suggestion": "briefing.action.installSuggestion",
  "connect-knowledge": "briefing.action.connectKnowledge",
  "check-health": "briefing.action.checkHealth",
  "ask-brain": "briefing.action.askBrain",
};

function section(data: Record<string, unknown>, key: string): Record<string, unknown> {
  const sections = (data.sections || {}) as Record<string, unknown>;
  const value = sections[key];
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function DailyBriefingPanel({ language }: { language: Language }) {
  const [expanded, setExpanded] = React.useState(false);
  const briefingQ = useQuery({
    queryKey: ["commandBriefing"],
    queryFn: latticeApi.commandBriefing,
    enabled: expanded,
  });

  const data = (briefingQ.data?.data || {}) as Record<string, unknown>;
  const knowledge = section(data, "knowledge");
  const conversations = section(data, "conversations");
  const automations = section(data, "automations");
  const review = section(data, "review");
  const health = section(data, "health");
  const suggestions = section(data, "suggestions");
  const quickActions = asArray<QuickAction>(data.quick_actions);
  const recent = asArray<Record<string, unknown>>(knowledge.recent);

  return (
    <section
      className={`brain-care-panel daily-briefing-panel ${expanded ? "is-expanded" : "is-collapsed"}`}
      aria-label={t(language, "briefing.title")}
      data-testid="daily-briefing"
    >
      <button
        className="brain-care-summary"
        type="button"
        aria-expanded={expanded}
        aria-controls="daily-briefing-details"
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="brain-care-summary-main">
          <span><Sunrise className="h-3.5 w-3.5" /> {t(language, "briefing.title")}</span>
          <strong>{t(language, "briefing.subtitle")}</strong>
        </span>
        <ChevronDown className="brain-care-toggle h-4 w-4" aria-hidden="true" />
      </button>

      {expanded ? (
        <div id="daily-briefing-details" className="brain-care-details">
          {briefingQ.isPending ? (
            <p className="brain-care-note">{t(language, "briefing.loading")}</p>
          ) : (
            <>
              <div className="daily-briefing-stats" role="list">
                <div role="listitem" className="daily-briefing-stat">
                  <strong>{String(conversations.questions ?? 0)}</strong>
                  <span>{t(language, "briefing.stat.questions")}</span>
                </div>
                <div role="listitem" className="daily-briefing-stat">
                  <strong>{String(automations.enabled ?? 0)}/{String(automations.total ?? 0)}</strong>
                  <span>{t(language, "briefing.stat.automations")}</span>
                </div>
                <div role="listitem" className="daily-briefing-stat">
                  <strong>{String(review.pending ?? 0)}</strong>
                  <span>{t(language, "briefing.stat.review")}</span>
                </div>
                <div role="listitem" className="daily-briefing-stat">
                  <strong>{health.available && health.grade ? String(health.grade) : "—"}</strong>
                  <span>{t(language, "briefing.stat.health")}</span>
                </div>
              </div>

              {recent.length > 0 ? (
                <div className="daily-briefing-recent">
                  <p className="command-palette-group">{t(language, "briefing.recent")}</p>
                  <ul>
                    {recent.slice(0, 3).map((node) => (
                      <li key={String(node.id)} title={String(node.title || "")}>
                        {String(node.title || node.id || "")}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {Number(suggestions.count ?? 0) > 0 ? (
                <p className="brain-care-note">
                  {t(language, "briefing.suggestions", { count: String(suggestions.count) })}
                </p>
              ) : null}

              {quickActions.length > 0 ? (
                <div className="daily-briefing-actions">
                  {quickActions.map((action) => {
                    const labelKey = ACTION_LABEL_KEYS[action.id];
                    if (!labelKey) return null;
                    return (
                      <button
                        key={action.id}
                        type="button"
                        className="daily-briefing-action"
                        onClick={() => navigateHash(action.target)}
                      >
                        <span>{t(language, labelKey, { count: String(action.count || 0) })}</span>
                        <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}
