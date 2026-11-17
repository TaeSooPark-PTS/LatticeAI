import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Sparkles, Trash2, UserRound, X } from "lucide-react";

import { latticeApi, type SelfModelFact } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { navigateHash } from "@/features/brain/navigation";
import { t } from "@/i18n";
import { asArray } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";

const SELF_KINDS = ["preference", "habit", "decision", "trait", "relationship"] as const;

type PendingSelfProposal = {
  id: string;
  kind: string;
  text: string;
};

export function extractPendingSelfProposals(raw: unknown): PendingSelfProposal[] {
  const rec = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const items = asArray<Record<string, unknown>>(rec.items ?? raw);
  const out: PendingSelfProposal[] = [];
  for (const item of items) {
    if (item.kind !== "self_model_fact") continue;
    const status = String(item.effective_status ?? item.status ?? "pending");
    if (status !== "pending") continue;
    const id = String(item.id ?? "");
    const payload =
      item.payload && typeof item.payload === "object"
        ? (item.payload as Record<string, unknown>)
        : {};
    const factKind = String(payload.self_model_kind ?? "preference");
    const text = String(payload.text ?? item.title ?? "").trim();
    if (id && text) {
      out.push({ id, kind: factKind, text });
    }
  }
  return out;
}

export function SelfModelPanel() {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const [kind, setKind] = React.useState<string>("preference");
  const [text, setText] = React.useState("");

  const profile = useQuery({ queryKey: ["selfModel"], queryFn: latticeApi.selfModel });
  const reviews = useQuery({
    queryKey: ["selfModelReviews"],
    queryFn: () => latticeApi.automationReviews({ status: "pending" }),
  });

  const invalidateAll = async () => {
    await qc.invalidateQueries({ queryKey: ["selfModel"] });
    await qc.invalidateQueries({ queryKey: ["selfModelReviews"] });
  };

  const addFact = useMutation({
    mutationFn: () => latticeApi.upsertSelfModelFact(kind, text.trim()),
    onSuccess: async (res) => {
      if (res.ok) {
        setText("");
        await invalidateAll();
      }
    },
  });

  const removeFact = useMutation({
    mutationFn: (nodeId: string) => latticeApi.deleteSelfModelFact(nodeId),
    onSuccess: invalidateAll,
  });

  const approveProposal = useMutation({
    mutationFn: (itemId: string) => latticeApi.applySelfModelProposal(itemId),
    onSuccess: invalidateAll,
  });

  const dismissProposal = useMutation({
    mutationFn: (itemId: string) => latticeApi.dismissReviewItem(itemId),
    onSuccess: invalidateAll,
  });

  const snapshot = profile.data?.data;
  const facts: SelfModelFact[] = asArray<SelfModelFact>(snapshot?.facts);
  const summary = typeof snapshot?.summary === "string" ? snapshot.summary.trim() : "";
  const proposals = extractPendingSelfProposals(reviews.data?.data);

  return (
    <Card data-testid="self-model-panel">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <UserRound className="h-4 w-4" aria-hidden="true" />
              {t(language, "brain.selfModel.title")}
            </CardTitle>
            <CardDescription>{t(language, "brain.selfModel.detail")}</CardDescription>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="self-model-open-review"
            onClick={() => navigateHash("/review")}
          >
            {t(language, "brain.selfModel.openReviewCenter")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {summary ? (
          <div className="rounded-lg border border-border bg-secondary/30 p-3 text-xs space-y-1">
            <div className="font-medium text-muted-foreground">
              {t(language, "brain.selfModel.summaryLabel")}
            </div>
            <pre className="whitespace-pre-wrap font-sans text-foreground" data-testid="self-model-summary">
              {summary}
            </pre>
          </div>
        ) : null}

        {proposals.length > 0 ? (
          <div className="rounded-lg border border-border p-3 space-y-2" data-testid="self-model-proposals">
            <div className="flex items-center gap-1.5 text-xs font-medium">
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              {t(language, "brain.selfModel.proposalsTitle", { count: proposals.length })}
            </div>
            <div className="space-y-2">
              {proposals.map((prop) => (
                <div
                  key={prop.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-secondary/40 px-3 py-2 text-sm"
                  data-testid="self-model-proposal-row"
                >
                  <div className="flex items-center gap-2">
                    <Badge variant="muted">
                      {t(language, `brain.selfModel.kind.${prop.kind}`)}
                    </Badge>
                    <span>{prop.text}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => approveProposal.mutate(prop.id)}
                      disabled={approveProposal.isPending}
                    >
                      <Check className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                      {t(language, "brain.selfModel.approve")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => dismissProposal.mutate(prop.id)}
                      disabled={dismissProposal.isPending}
                    >
                      <X className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                      {t(language, "brain.selfModel.dismiss")}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {facts.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="self-model-empty">
            {t(language, "brain.selfModel.empty")}
          </p>
        ) : (
          <div className="space-y-2" data-testid="self-model-facts">
            {facts.map((fact) => (
              <div
                key={fact.id}
                className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm"
                data-testid="self-model-fact-row"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="default">
                    {t(language, `brain.selfModel.kind.${fact.kind}`)}
                  </Badge>
                  <span>{fact.text}</span>
                  {fact.origin ? (
                    <Badge variant="muted">
                      {t(
                        language,
                        fact.origin === "user"
                          ? "brain.selfModel.origin.user"
                          : "brain.selfModel.origin.approved",
                      )}
                    </Badge>
                  ) : null}
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`${t(language, "brain.selfModel.delete")} ${fact.text}`}
                  onClick={() => removeFact.mutate(fact.id)}
                  disabled={removeFact.isPending}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
              </div>
            ))}
          </div>
        )}

        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault();
            if (text.trim()) addFact.mutate();
          }}
        >
          <select
            aria-label={t(language, "brain.selfModel.title")}
            className="rounded-md border border-border bg-background px-2.5 py-1.5 text-sm"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
            data-testid="self-model-kind-select"
          >
            {SELF_KINDS.map((k) => (
              <option key={k} value={k}>
                {t(language, `brain.selfModel.kind.${k}`)}
              </option>
            ))}
          </select>
          <Input
            placeholder={t(language, "brain.selfModel.addPlaceholder")}
            value={text}
            onChange={(event) => setText(event.target.value)}
            data-testid="self-model-input"
          />
          <Button type="submit" disabled={!text.trim() || addFact.isPending} data-testid="self-model-submit">
            {t(language, "brain.selfModel.addCta")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
