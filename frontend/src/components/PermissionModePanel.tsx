import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";

import { latticeApi, type PermissionModeOption, type PermissionModeState } from "@/api/client";
import { EmptyState } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";

const RISK_VARIANT: Record<string, "success" | "warning" | "danger"> = {
  low: "success",
  medium: "warning",
  high: "danger",
};

/** Catalog copy is served localized; pick the field for the active language. */
function optionLabel(option: PermissionModeOption, language: Language): string {
  return language === "ko" ? option.label_ko || option.label : option.label;
}

function optionSummary(option: PermissionModeOption, language: Language): string {
  return language === "ko" ? option.summary_ko || option.summary : option.summary;
}

function optionWarning(option: PermissionModeOption, language: Language): string {
  const warning = language === "ko" ? option.warning_ko || option.warning : option.warning;
  return warning || "";
}

/**
 * The autonomy dial (v9.9.8).
 *
 * Renders whatever `/api/permission-mode` reports rather than a hardcoded mode
 * list, so the server stays the single source of truth for which modes exist
 * and which one needs an explicit risk acknowledgement.
 */
export function PermissionModePanel() {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [acknowledged, setAcknowledged] = React.useState(false);

  const state = useQuery({ queryKey: ["permissionMode"], queryFn: latticeApi.permissionMode });
  const apply = useMutation({
    mutationFn: (input: { mode: string; ack: boolean }) =>
      latticeApi.setPermissionMode(input.mode, input.ack),
    onSuccess: (result) => {
      if (!result.ok) return;
      qc.invalidateQueries({ queryKey: ["permissionMode"] });
      setSelected(null);
      setAcknowledged(false);
    },
  });

  const data: PermissionModeState | undefined = state.data?.ok ? state.data.data : undefined;
  const catalog = data?.catalog ?? [];
  const active = data?.mode ?? "";
  // Before any interaction the active mode is the selection, so the panel opens
  // showing what is actually in force rather than an empty form.
  const draft = selected ?? active;
  const draftOption = catalog.find((option) => option.id === draft);
  const needsAck = Boolean(draftOption?.requires_ack) && draft !== active;
  const blocked = needsAck && !acknowledged;
  const unchanged = draft === active;

  if (state.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.permission.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{t(language, "ui.loading")}</p>
        </CardContent>
      </Card>
    );
  }

  if (!data || catalog.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.permission.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title={t(language, "ui.requestUnavailable")}
            detail={state.data?.error}
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="permission-mode-panel">
      <CardHeader>
        <CardTitle>{t(language, "system.permission.title")}</CardTitle>
        <CardDescription>{t(language, "system.permission.hint")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {t(language, "system.permission.current")}:{" "}
          <strong data-testid="permission-mode-active">
            {language === "ko" ? data.label_ko || data.label : data.label}
          </strong>
        </p>

        <div className="space-y-2" role="radiogroup" aria-label={t(language, "system.permission.title")}>
          {catalog.map((option) => {
            const isDraft = option.id === draft;
            return (
              <button
                key={option.id}
                type="button"
                role="radio"
                aria-checked={isDraft}
                data-testid={`permission-mode-option-${option.id}`}
                onClick={() => {
                  setSelected(option.id);
                  setAcknowledged(false);
                  apply.reset();
                }}
                className={`w-full rounded-md border p-3 text-left transition ${
                  isDraft ? "border-primary bg-accent" : "border-border hover:bg-accent/50"
                }`}
              >
                <span className="flex items-center gap-2">
                  <strong className="text-sm">{optionLabel(option, language)}</strong>
                  <Badge variant={RISK_VARIANT[option.risk] || "warning"}>
                    {t(language, `system.permission.risk.${option.risk}`)}
                  </Badge>
                  {option.id === active ? (
                    <Badge variant="muted">{t(language, "system.permission.current")}</Badge>
                  ) : null}
                </span>
                <p className="mt-1 block text-xs text-muted-foreground">
                  {optionSummary(option, language)}
                </p>
              </button>
            );
          })}
        </div>

        {needsAck ? (
          <div className="space-y-2 rounded-md border border-border bg-muted p-3">
            <p className="flex items-start gap-2 text-xs">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{draftOption ? optionWarning(draftOption, language) : ""}</span>
            </p>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                data-testid="permission-mode-ack"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
              {t(language, "system.permission.ack")}
            </label>
          </div>
        ) : null}

        <Button
          data-testid="permission-mode-apply"
          disabled={unchanged || blocked || apply.isPending}
          onClick={() => apply.mutate({ mode: draft, ack: acknowledged })}
        >
          {t(language, "system.permission.apply")}
        </Button>

        {apply.data && !apply.data.ok ? (
          <EmptyState title={t(language, "ui.requestUnavailable")} detail={apply.data.error} />
        ) : null}
        {apply.data?.ok ? (
          <Badge variant="success" data-testid="permission-mode-applied">
            {t(language, "ui.requestCompleted")}
          </Badge>
        ) : null}

        <div className="rounded-md border border-border p-3">
          <p className="text-xs font-medium">{t(language, "system.permission.guards")}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t(language, "system.permission.guards.detail")}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
