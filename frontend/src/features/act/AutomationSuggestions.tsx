import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderOpen, MessageCircleQuestion, ShieldCheck, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { asArray } from "@/lib/utils";
import { t, type Language } from "@/i18n";

type Suggestion = {
  id: string;
  kind: string;
  title: string;
  cadence: string;
  installed: boolean;
  reason: Record<string, unknown>;
};

export function AutomationSuggestions({ language }: { language: Language }) {
  const qc = useQueryClient();
  const overview = useQuery({ queryKey: ["automationOverview"], queryFn: latticeApi.automationOverview });
  const install = useMutation({
    mutationFn: (suggestionId: string) => latticeApi.installAutomationSuggestion(suggestionId, false),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["automationOverview"] }),
        qc.invalidateQueries({ queryKey: ["workflowDefinitions"] }),
      ]);
    },
  });

  const data = (overview.data?.data || {}) as Record<string, unknown>;
  const suggestions = asArray<Suggestion>(data.suggestions);
  const scanned = Number(data.questions_scanned || 0);

  return (
    <Card className="automation-suggestions xl:col-span-2" data-testid="automation-suggestions">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="h-4 w-4" /> {t(language, "act.suggest.title")}
        </CardTitle>
        <CardDescription>
          {t(language, "act.suggest.subtitle")}
          {scanned > 0 ? ` · ${t(language, "act.suggest.scanned", { count: String(scanned) })}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {suggestions.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t(language, "act.suggest.empty")}</p>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {suggestions.map((suggestion) => {
              const isFolder = suggestion.kind === "knowledge_source";
              const reason = (suggestion.reason || {}) as Record<string, unknown>;
              const evidence = isFolder
                ? t(language, "act.suggest.evidence.folder", { count: String(reason.indexed_files ?? 0) })
                : t(language, "act.suggest.evidence.count", { count: String(reason.count ?? 0) });
              const cadence = isFolder
                ? t(language, "act.suggest.cadence.newKnowledge")
                : t(language, "act.suggest.cadence.daily");
              const isInstalling = install.isPending && install.variables === suggestion.id;
              return (
                <div key={suggestion.id} className="rounded-lg border border-border bg-background/70 p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs font-semibold uppercase text-muted-foreground">
                        {isFolder
                          ? <><FolderOpen className="h-3.5 w-3.5" /> {t(language, "act.suggest.folder")}</>
                          : <><MessageCircleQuestion className="h-3.5 w-3.5" /> {t(language, "act.suggest.question")}</>}
                      </div>
                      <p className="mt-1 truncate font-medium" title={suggestion.title}>{suggestion.title}</p>
                    </div>
                    <Badge variant="muted">{cadence}</Badge>
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">{evidence}</p>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <Badge variant="success">
                      <ShieldCheck className="h-3 w-3" /> {t(language, "act.automation.local")}
                    </Badge>
                    {suggestion.installed ? (
                      <Badge variant="warning">{t(language, "act.suggest.installed")}</Badge>
                    ) : (
                      <Button size="sm" disabled={install.isPending} onClick={() => install.mutate(suggestion.id)}>
                        {isInstalling ? t(language, "act.suggest.installing") : t(language, "act.suggest.install")}
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <p className="mt-3 text-xs text-muted-foreground">{t(language, "act.suggest.note")}</p>
      </CardContent>
    </Card>
  );
}
