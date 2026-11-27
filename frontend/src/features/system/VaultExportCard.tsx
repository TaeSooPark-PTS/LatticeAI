import { useMutation } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";

export function VaultExportCard() {
  const language = useAppStore((state) => state.language);
  const workspaceId = useAppStore((state) => state.workspaceId);
  const vault = useMutation({ mutationFn: () => latticeApi.exportBrainVault(workspaceId || "personal") });
  const result = vault.data;

  return (
    <Card data-testid="vault-export-card">
      <CardHeader>
        <CardTitle>{t(language, "system.vault.title")}</CardTitle>
        <CardDescription>{t(language, "system.vault.detail")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-3">
        <Button variant="outline" onClick={() => vault.mutate()} disabled={vault.isPending} aria-busy={vault.isPending}>
          {t(language, vault.isPending ? "system.vault.exporting" : "system.vault.export")}
        </Button>
        {!vault.isPending && result?.ok ? (
          <div role="status" className="text-sm text-muted-foreground tabular-nums">
            <p>
              {result.notes
                ? t(language, "system.vault.done", { notes: result.notes, history: result.history })
                : t(language, "system.vault.empty")}
            </p>
            {result.withheld ? <p>{t(language, "system.vault.withheld", { count: result.withheld })}</p> : null}
            {result.omitted || result.truncated ? (
              <p>{t(language, "system.vault.limited", { omitted: result.omitted, truncated: result.truncated })}</p>
            ) : null}
          </div>
        ) : null}
        {!vault.isPending && result && !result.ok ? (
          <p role="alert" className="text-sm text-destructive">
            {t(language, "system.vault.failed", { error: String(result.error) })}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
