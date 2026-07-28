import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, ShieldAlert } from "lucide-react";

import {
  latticeApi,
  type CloudContextPreview,
  type NetworkBoundaryOption,
  type NetworkBoundaryState,
} from "@/api/client";
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
function optionLabel(option: NetworkBoundaryOption, language: Language): string {
  return language === "ko" ? option.label_ko || option.label : option.label;
}

function optionSummary(option: NetworkBoundaryOption, language: Language): string {
  return language === "ko" ? option.summary_ko || option.summary : option.summary;
}

function optionWarning(option: NetworkBoundaryOption, language: Language): string {
  const warning = language === "ko" ? option.warning_ko || option.warning : option.warning;
  return warning || "";
}

/**
 * The network boundary dial (v10.1.1).
 *
 * The contracts and the API shipped in 10.1.0, but nothing in the app could
 * reach them — the dial existed only for whoever called `/api/network-boundary`
 * by hand. This is that control.
 *
 * Two things make it different from the autonomy dial next to it. It answers
 * "may knowledge leave this machine" rather than "may this tool run", so the
 * two are set independently. And it can show the user *the actual memories*
 * a question would send, before anything is sent — a promise about data
 * leaving the machine is worth less than a list of what leaves.
 */
export function NetworkBoundaryPanel() {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [acknowledged, setAcknowledged] = React.useState(false);
  const [probe, setProbe] = React.useState("");

  const state = useQuery({ queryKey: ["networkBoundary"], queryFn: latticeApi.networkBoundary });
  const apply = useMutation({
    mutationFn: (input: { mode: string; ack: boolean }) =>
      latticeApi.setNetworkBoundary(input.mode, input.ack),
    onSuccess: (result) => {
      if (!result.ok) return;
      qc.invalidateQueries({ queryKey: ["networkBoundary"] });
      setSelected(null);
      setAcknowledged(false);
    },
  });
  const savePolicy = useMutation({
    mutationFn: (patch: { auto_commit?: boolean; allow_multimodal?: boolean }) =>
      latticeApi.setHybridPolicy(patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["networkBoundary"] }),
  });
  const preview = useMutation({
    mutationFn: (message: string) => latticeApi.previewCloudContext(message),
  });

  const data: NetworkBoundaryState | undefined = state.data?.ok ? state.data.data : undefined;
  const catalog = data?.catalog ?? [];
  const active = data?.mode ?? "";
  // Open showing what is actually in force, not an empty form.
  const draft = selected ?? active;
  const draftOption = catalog.find((option) => option.id === draft);
  const needsAck = Boolean(draftOption?.requires_ack) && draft !== active;
  const blocked = needsAck && !acknowledged;
  const unchanged = draft === active;
  const policy = data?.policy ?? {};
  const previewData: CloudContextPreview | undefined = preview.data?.ok
    ? preview.data.data
    : undefined;

  if (state.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.network.title")}</CardTitle>
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
          <CardTitle>{t(language, "system.network.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState title={t(language, "ui.requestUnavailable")} detail={state.data?.error} />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="network-boundary-panel">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Globe className="h-4 w-4" aria-hidden="true" />
          {t(language, "system.network.title")}
        </CardTitle>
        <CardDescription>{t(language, "system.network.hint")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {t(language, "system.network.current")}:{" "}
          <strong data-testid="network-boundary-active">
            {language === "ko" ? data.label_ko || data.label : data.label}
          </strong>
        </p>

        <div
          className="space-y-2"
          role="radiogroup"
          aria-label={t(language, "system.network.title")}
        >
          {catalog.map((option) => {
            const isDraft = option.id === draft;
            return (
              <button
                key={option.id}
                type="button"
                role="radio"
                aria-checked={isDraft}
                data-testid={`network-boundary-option-${option.id}`}
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
                    <Badge variant="muted">{t(language, "system.network.current")}</Badge>
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
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span>{draftOption ? optionWarning(draftOption, language) : ""}</span>
            </p>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                data-testid="network-boundary-ack"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
              {t(language, "system.network.ack")}
            </label>
          </div>
        ) : null}

        <Button
          data-testid="network-boundary-apply"
          disabled={unchanged || blocked || apply.isPending}
          onClick={() => apply.mutate({ mode: draft, ack: acknowledged })}
        >
          {t(language, "system.network.apply")}
        </Button>

        {apply.data && !apply.data.ok ? (
          <EmptyState title={t(language, "ui.requestUnavailable")} detail={apply.data.error} />
        ) : null}
        {apply.data?.ok ? (
          <Badge variant="success" data-testid="network-boundary-applied">
            {t(language, "ui.requestCompleted")}
          </Badge>
        ) : null}

        {/* Transparency before consent: the user can name a question and see the
            exact memories it would send. Available in local_only too — you
            should be able to look before you decide, not only after. */}
        <div className="space-y-2 rounded-md border border-border p-3">
          <p className="text-xs font-medium">{t(language, "system.network.preview.title")}</p>
          <p className="text-xs text-muted-foreground">
            {t(language, "system.network.preview.hint")}
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              type="text"
              data-testid="network-boundary-probe"
              value={probe}
              onChange={(event) => setProbe(event.target.value)}
              placeholder={t(language, "system.network.preview.placeholder")}
              className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1 text-xs"
            />
            <Button
              variant="outline"
              data-testid="network-boundary-preview"
              disabled={!probe.trim() || preview.isPending}
              onClick={() => preview.mutate(probe.trim())}
            >
              {t(language, "system.network.preview.run")}
            </Button>
          </div>

          {previewData ? (
            <div className="space-y-1" data-testid="network-boundary-preview-result">
              {!previewData.allows_cloud ? (
                <p className="text-xs text-muted-foreground">
                  {t(language, "system.network.preview.localOnly")}
                </p>
              ) : null}
              {previewData.would_block ? (
                <Badge variant="danger">
                  {t(language, "system.network.preview.blocked")}
                </Badge>
              ) : null}
              {previewData.titles.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t(language, "system.network.preview.empty")}
                </p>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground">
                    {t(language, "system.network.preview.count", {
                      count: previewData.titles.length,
                    })}
                    {" · "}
                    {t(language, "system.network.preview.tokens", {
                      tokens: previewData.token_estimate,
                    })}
                  </p>
                  <ul className="space-y-0.5">
                    {previewData.titles.map((title, index) => (
                      <li key={`${title}-${index}`} className="text-xs">
                        · {title}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          ) : null}
          {preview.data && !preview.data.ok ? (
            <EmptyState title={t(language, "ui.requestUnavailable")} detail={preview.data.error} />
          ) : null}
        </div>

        {/* Write-back policy. Only meaningful once cloud is permitted, so it
            stays hidden in local_only rather than offering dead switches. */}
        {data.allows_cloud ? (
          <div className="space-y-2 rounded-md border border-border p-3" data-testid="network-boundary-policy">
            <p className="text-xs font-medium">{t(language, "system.network.policy.title")}</p>
            <label className="flex items-start gap-2 text-xs">
              <input
                type="checkbox"
                data-testid="network-boundary-auto-commit"
                checked={Boolean(policy.auto_commit)}
                disabled={savePolicy.isPending}
                onChange={(event) => savePolicy.mutate({ auto_commit: event.target.checked })}
              />
              <span>
                {t(language, "system.network.policy.autoCommit")}
                <span className="mt-0.5 block text-muted-foreground">
                  {t(language, "system.network.policy.autoCommitHint")}
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-xs">
              <input
                type="checkbox"
                data-testid="network-boundary-multimodal"
                checked={Boolean(policy.allow_multimodal)}
                disabled={savePolicy.isPending}
                onChange={(event) => savePolicy.mutate({ allow_multimodal: event.target.checked })}
              />
              <span>
                {t(language, "system.network.policy.multimodal")}
                <span className="mt-0.5 block text-muted-foreground">
                  {t(language, "system.network.policy.multimodalHint")}
                </span>
              </span>
            </label>
          </div>
        ) : null}

        <div className="rounded-md border border-border p-3">
          <p className="text-xs font-medium">{t(language, "system.network.guards")}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t(language, "system.network.guards.detail")}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
