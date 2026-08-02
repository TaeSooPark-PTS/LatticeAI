import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";

import { latticeApi, type PermissionModeOption } from "@/api/client";
import { t, type Language } from "@/i18n";
// Shared with the Settings panel so both surfaces name the same dial the same
// way — see lib/permissionCopy.ts for why the server's own labels are not used
// directly.
import {
  permissionModeLabel as optionLabel,
  permissionModeSummary as optionSummary,
  permissionModeWarning as optionWarning,
} from "@/lib/permissionCopy";

const RISK_DOT: Record<string, string> = {
  low: "is-low",
  medium: "is-medium",
  high: "is-high",
};

/**
 * Autonomy and appearance, in reach on the home screen.
 *
 * Both used to live two navigations away in Settings, which is the wrong place
 * for them: how much the agent may do on its own is a decision you revise while
 * working, not a preference you set once. The dial reads its options from
 * `/api/permission-mode` rather than hardcoding them, and a mode the server
 * marks `requires_ack` opens an inline confirmation here instead of failing.
 */
export function BrainQuickControls({ language }: { language: Language }) {
  const qc = useQueryClient();
  const [pendingRisky, setPendingRisky] = React.useState<string | null>(null);

  const state = useQuery({ queryKey: ["permissionMode"], queryFn: latticeApi.permissionMode });
  const apply = useMutation({
    mutationFn: (input: { mode: string; ack: boolean }) =>
      latticeApi.setPermissionMode(input.mode, input.ack),
    onSuccess: (result) => {
      if (!result.ok) return;
      qc.invalidateQueries({ queryKey: ["permissionMode"] });
      setPendingRisky(null);
    },
  });

  const data = state.data?.ok ? state.data.data : undefined;
  const catalog = data?.catalog ?? [];
  const active = data?.mode ?? "";
  const riskyOption = catalog.find((option) => option.id === pendingRisky);

  function choose(option: PermissionModeOption) {
    if (option.id === active) return;
    if (option.requires_ack) {
      setPendingRisky(option.id);
      return;
    }
    setPendingRisky(null);
    apply.mutate({ mode: option.id, ack: false });
  }

  return (
    <div className="brain-quick-controls" data-testid="brain-quick-controls">
      {catalog.length ? (
        <div
          className="brain-quick-dial"
          role="radiogroup"
          aria-label={t(language, "system.permission.title")}
        >
          <span className="brain-quick-label">{t(language, "system.permission.title")}</span>
          <div className="brain-quick-segments">
            {catalog.map((option) => (
              <button
                key={option.id}
                type="button"
                role="radio"
                aria-checked={option.id === active}
                className={option.id === active ? "is-active" : ""}
                data-testid={`quick-mode-${option.id}`}
                title={optionSummary(option, language)}
                disabled={apply.isPending}
                onClick={() => choose(option)}
              >
                <i className={`brain-quick-dot ${RISK_DOT[option.risk] || "is-medium"}`} aria-hidden="true" />
                {optionLabel(option, language)}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {riskyOption ? (
        <div className="brain-quick-confirm" role="alertdialog" data-testid="quick-mode-confirm">
          <ShieldAlert className="h-4 w-4" aria-hidden="true" />
          <p>{optionWarning(riskyOption, language)}</p>
          <button
            type="button"
            className="is-primary"
            data-testid="quick-mode-confirm-apply"
            disabled={apply.isPending}
            onClick={() => apply.mutate({ mode: riskyOption.id, ack: true })}
          >
            {optionLabel(riskyOption, language)}
          </button>
          <button type="button" onClick={() => setPendingRisky(null)}>
            {t(language, "ui.cancel")}
          </button>
        </div>
      ) : null}

      {apply.data && !apply.data.ok ? (
        <p className="brain-quick-error" role="status">
          {apply.data.error || t(language, "ui.requestUnavailable")}
        </p>
      ) : null}
    </div>
  );
}
