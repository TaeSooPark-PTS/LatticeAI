import { PackagePlus, Plug } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, OperationResult } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n";
import { asArray } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";
import { useMarketplaceCatalog, useMcpCatalog, useSkillsCatalog } from "./hooks/useLibraryCatalogs";

export function SkillsPanel() {
  const language = useAppStore((state) => state.language);
  const { skills, market, install } = useSkillsCatalog();

  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, "library.skills.installed")} result={skills.data}>
        {(data) => (
          <div className="flex flex-col gap-2">
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).skills).map((skill) => (
              <div key={String(skill.name || skill.id)} className="flex items-center justify-between gap-3 rounded-md border border-border p-3">
                <div>
                  <div className="font-medium">{String(skill.name || skill.id)}</div>
                  <div className="text-sm text-muted-foreground">{String(skill.plugin || skill.description || "")}</div>
                </div>
                <ActionButton label={t(language, skill.enabled ? "library.skills.disable" : "library.skills.enable")} action={() => latticeApi.skillToggle(String(skill.name || skill.id), Boolean(skill.enabled))} invalidate={["skills"]} />
              </div>
            ))}
          </div>
        )}
      </DataPanel>
      <DataPanel title={t(language, "library.skills.marketplace")} result={market.data}>
        {(data) => (
          <div className="flex flex-col gap-2">
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).skills).slice(0, 10).map((skill) => (
              <div key={String(skill.name || skill.id)} className="flex items-center justify-between gap-3 rounded-md border border-border p-3">
                <div>
                  <div className="font-medium">{String(skill.name || skill.id)}</div>
                  <div className="text-sm text-muted-foreground">{String(skill.description || skill.category || "")}</div>
                </div>
                <Button variant="outline" disabled={install.isPending} onClick={() => install.mutate(skill)}>{t(language, "library.skills.install")}</Button>
              </div>
            ))}
          </div>
        )}
      </DataPanel>
    </div>
  );
}

export function McpPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const { query, setQuery, tools, rec } = useMcpCatalog();

  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, mode === "basic" ? "library.connector.connections" : "library.connector.mcpTools")} result={tools.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).tools || (data as Record<string, unknown>).installed_mcps} titleKey="name" metaKey="status" />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Plug className="h-4 w-4" /> {t(language, "library.connector.recommend")}</CardTitle>
          <CardDescription>{t(language, "library.connector.recommendHint")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input value={query} onChange={(e) => setQuery(e.target.value)} />
            <Button onClick={() => rec.mutate()} disabled={!query.trim() || rec.isPending}>{t(language, "library.connector.recommendAction")}</Button>
          </div>
          {rec.data ? <OperationResult result={rec.data} successLabel={t(language, "library.connector.recommendDone")} /> : null}
        </CardContent>
      </Card>
    </div>
  );
}

export function MarketplacePanel() {
  const language = useAppStore((state) => state.language);
  const { templates, plugins, dir } = useMarketplaceCatalog();

  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, "library.market.templates")} result={templates.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).templates} titleKey="name" metaKey="kind" />}
      </DataPanel>
      <DataPanel title={t(language, "library.market.installedPlugins")} result={plugins.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).plugins} titleKey="name" metaKey="status" />}
      </DataPanel>
      <DataPanel title={t(language, "library.market.pluginDirectory")} result={dir.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).plugins} titleKey="name" metaKey="category" />}
      </DataPanel>
      <Card className="xl:col-span-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><PackagePlus className="h-4 w-4" /> {t(language, "library.market.templateInstall")}</CardTitle>
          <CardDescription>{t(language, "library.market.templateInstallHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {asArray<Record<string, unknown>>((templates.data?.data as Record<string, unknown>)?.templates).length ? (
            <div className="flex flex-wrap gap-2">
              {asArray<Record<string, unknown>>((templates.data?.data as Record<string, unknown>)?.templates).slice(0, 6).map((template) => (
                <ActionButton key={String(template.id || template.name)} label={t(language, "library.market.installTemplate", { name: String(template.name || template.id) })} action={() => latticeApi.installTemplate(template)} />
              ))}
            </div>
          ) : <EntityList items={[]} />}
        </CardContent>
      </Card>
    </div>
  );
}
