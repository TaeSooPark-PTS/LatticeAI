import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info } from "lucide-react";

import { latticeApi, type FeatureCatalog, type FeatureToggle } from "@/api/client";
import { EmptyState } from "@/components/primitives";
import { t, type Language } from "@/i18n";

export const FEATURES_QUERY_KEY = ["features"] as const;

/**
 * The opt-in switchboard (v11.2.0).
 *
 * Everything on screen is the server's: ids, labels, one-line explanations,
 * defaults, and which options are even installable. The panel holds no feature
 * list of its own, so a switch added to `feature_toggles.CATALOG` appears here
 * with no client change — and, more importantly, the panel can never show a
 * switch the server would refuse.
 *
 * Three honesty rules it renders rather than hides:
 *
 * * a feature answered by an environment variable says so, instead of looking
 *   like something this person chose;
 * * the one switch that sends knowledge off this machine carries its caution
 *   line next to it, not in a doc;
 * * an option whose optional dependency is missing is shown *disabled with the
 *   reason*, because a hidden option is a mystery and a live one is a lie.
 */
export function BrainFeaturesPanel({ language }: { language: Language }) {
  const qc = useQueryClient();
  const [notice, setNotice] = React.useState("");
  const state = useQuery({ queryKey: FEATURES_QUERY_KEY, queryFn: latticeApi.features });

  const apply = useMutation({
    mutationFn: (input: { id: string; value: boolean | string }) =>
      latticeApi.setFeature(input.id, input.value),
    // Optimistic: a switch has to move under the finger. The pre-write catalog
    // is carried through as the rollback, so a refusal puts the switch back
    // where it was rather than leaving the UI claiming something the server
    // never agreed to.
    onMutate: async (input) => {
      await qc.cancelQueries({ queryKey: FEATURES_QUERY_KEY });
      const previous = qc.getQueryData(FEATURES_QUERY_KEY);
      qc.setQueryData(FEATURES_QUERY_KEY, (current: unknown) =>
        patchCatalog(current, input.id, input.value),
      );
      setNotice("");
      return { previous };
    },
    onSuccess: (result, _input, context) => {
      if (result.ok) {
        setNotice(t(language, "brain.features.saved"));
        void qc.invalidateQueries({ queryKey: FEATURES_QUERY_KEY });
        return;
      }
      qc.setQueryData(FEATURES_QUERY_KEY, context?.previous);
      setNotice(result.error || t(language, "brain.features.failed"));
    },
    onError: (_error, _input, context) => {
      qc.setQueryData(FEATURES_QUERY_KEY, context?.previous);
      setNotice(t(language, "brain.features.failed"));
    },
  });

  const catalog: FeatureCatalog | undefined = state.data?.ok ? state.data.data : undefined;
  const features = catalog?.features ?? [];
  const enabledIds = new Set(
    features.filter((feature) => feature.current === true).map((feature) => feature.id),
  );

  return (
    <section
      className="brain-features-panel"
      aria-label={t(language, "brain.features.aria")}
      aria-busy={apply.isPending}
    >
      <header className="brain-features-head">
        <strong>{t(language, "brain.features.title")}</strong>
        <p>{catalog?.note || t(language, "brain.features.note")}</p>
      </header>

      {state.isLoading ? (
        <p className="brain-features-loading">{t(language, "ui.loading")}</p>
      ) : null}

      {!state.isLoading && features.length === 0 ? (
        <EmptyState
          title={t(language, "brain.features.empty")}
          detail={state.data?.error}
        />
      ) : null}

      {features.length ? (
        <ul className="brain-features-list">
          {features.map((feature) => (
            <FeatureRow
              key={feature.id}
              feature={feature}
              language={language}
              // A sub-switch whose parent is off changes nothing yet. It stays
              // readable and movable — "set it up now, turn the parent on
              // later" is a real thing people do — but it says it is dormant.
              dormant={Boolean(feature.parent) && !enabledIds.has(String(feature.parent))}
              busy={apply.isPending}
              onChange={(value) => apply.mutate({ id: feature.id, value })}
            />
          ))}
        </ul>
      ) : null}

      <p className="brain-features-notice" role="status" aria-live="polite">
        {notice}
      </p>
    </section>
  );
}

function FeatureRow({
  feature,
  language,
  dormant,
  busy,
  onChange,
}: {
  feature: FeatureToggle;
  language: Language;
  dormant: boolean;
  busy: boolean;
  onChange: (value: boolean | string) => void;
}) {
  const on = feature.current === true;
  return (
    <li
      className={`brain-feature-row${feature.parent ? " is-child" : ""}${on ? " is-on" : ""}`}
      data-testid={`feature-row-${feature.id}`}
    >
      <div className="brain-feature-copy">
        <span className="brain-feature-name">
          {feature.label}
          {feature.source === "env" ? (
            <em
              className="brain-feature-source"
              title={t(language, "brain.features.source.env.detail", { flag: feature.env_var })}
            >
              {t(language, "brain.features.source.env")}
            </em>
          ) : null}
          {dormant ? (
            <em className="brain-feature-source">{t(language, "brain.features.dormant")}</em>
          ) : null}
        </span>
        <span className="brain-feature-summary">{feature.summary}</span>
        {feature.caution ? (
          <span className="brain-feature-caution">
            <Info className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {feature.caution}
          </span>
        ) : null}
      </div>

      {feature.kind === "choice" ? (
        <ChoicePills feature={feature} language={language} busy={busy} onChange={onChange} />
      ) : (
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label={feature.label}
          className="brain-feature-switch"
          data-testid={`feature-switch-${feature.id}`}
          // Never `disabled` while a write is in flight: disabling the button
          // blurs it, and the drawer's Escape handler lives on the drawer
          // node — focus on <body> means Escape stops closing the panel. The
          // in-flight guard is the click handler's job instead.
          onClick={() => {
            if (!busy) onChange(!on);
          }}
        >
          <span className="brain-feature-switch-track" aria-hidden="true">
            <span className="brain-feature-switch-thumb" />
          </span>
          <span className="brain-feature-state">
            {t(language, on ? "brain.features.on" : "brain.features.off")}
          </span>
        </button>
      )}
    </li>
  );
}

function ChoicePills({
  feature,
  language,
  busy,
  onChange,
}: {
  feature: FeatureToggle;
  language: Language;
  busy: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div
      className="brain-feature-choices"
      role="radiogroup"
      aria-label={feature.label}
      data-testid={`feature-choices-${feature.id}`}
    >
      {feature.choices.map((choice) => (
        <button
          key={choice.id}
          type="button"
          role="radio"
          aria-checked={feature.current === choice.id}
          className={feature.current === choice.id ? "is-active" : ""}
          data-testid={`feature-choice-${feature.id}-${choice.id}`}
          disabled={!choice.available}
          title={choice.detail || undefined}
          onClick={() => {
            if (!busy) onChange(choice.id);
          }}
        >
          {choice.label}
          {choice.available ? null : (
            <em>{t(language, "brain.features.installRequired")}</em>
          )}
        </button>
      ))}
    </div>
  );
}

/**
 * The optimistic edit, as a pure function over whatever the cache holds.
 *
 * Written defensively because the cache entry is an `ApiResult` that may be a
 * failed read: replacing a `{ ok: false }` entry with an optimistic catalog
 * would turn a broken read into a panel of plausible-looking switches.
 */
export function patchCatalog(current: unknown, id: string, value: boolean | string) {
  const result = current as { ok?: boolean; data?: FeatureCatalog } | undefined;
  if (!result?.ok || !result.data) return current;
  return {
    ...result,
    data: {
      ...result.data,
      features: result.data.features.map((feature) =>
        feature.id === id ? { ...feature, current: value, source: "user" } : feature,
      ),
    },
  };
}
