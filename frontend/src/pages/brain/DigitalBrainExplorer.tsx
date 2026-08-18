import * as React from "react";
import "@/i18n/brain";
import { useMutation } from "@tanstack/react-query";
import { Filter, Focus, LocateFixed, Search, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { DataPanel, EmptyState, EntityList, KeyValueList, StructuredView } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAppStore } from "@/store/appStore";
import { t } from "@/i18n";
import { fmtNumber } from "@/lib/utils";
import { CytoscapeGraph } from "./CytoscapeGraph";
import { buildExplorerModel, parseGraph, type LabelMode } from "./graphExplorer";
import { graphTypeLabel, groupLabelFor, sourceCreatedAt, sourceType } from "./sourceMeta";

export function DigitalBrainExplorer({ data }: { data: unknown }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const theme = useAppStore((state) => state.theme);
  const parsed = React.useMemo(() => parseGraph(data, language), [data, language, theme]);
  const [search, setSearch] = React.useState("");
  const [groupFilter, setGroupFilter] = React.useState("all");
  const [minImportance, setMinImportance] = React.useState(mode === "basic" ? 0.1 : 0);
  const [labelMode, setLabelMode] = React.useState<LabelMode>("important");
  const [collapsedGroups, setCollapsedGroups] = React.useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [fitSignal, setFitSignal] = React.useState(0);
  const backendSearch = useMutation({ mutationFn: () => latticeApi.hybridSearch(search.trim()) });
  const model = React.useMemo(() => buildExplorerModel({
    graph: parsed,
    search,
    groupFilter,
    minImportance,
    collapsedGroups,
    selectedId,
    labelMode,
    maxNodes: 220,
  }), [parsed, search, groupFilter, minImportance, collapsedGroups, selectedId, labelMode]);
  const selected = parsed.nodes.find((node) => node.id === selectedId);
  const selectedGroup = selectedId?.startsWith("group:") ? model.groups.find((group) => group.id === selectedId.replace("group:", "")) : null;
  const toggleGroup = (id: string) => {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  React.useEffect(() => {
    if (mode === "basic" && minImportance < 0.1) setMinImportance(0.1);
  }, [mode, minImportance]);
  if (!parsed.nodes.length) {
    return (
      <EmptyState
        title={t(language, "graph.empty.title")}
        detail={t(language, "graph.empty.detail")}
      />
    );
  }
  return (
    <div className="graph-explorer">
      <div className="graph-explorer-toolbar">
        <div className="graph-explorer-search">
          <Search className="graph-explorer-search-icon" aria-hidden="true" />
          <Input
            className="graph-explorer-search-input"
            aria-label={t(language, "graph.search.aria")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={mode === "basic" ? t(language, "graph.search.basic") : t(language, "graph.search.advanced")}
          />
        </div>
        <select className="graph-explorer-select" value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}>
          <option value="all">{t(language, "graph.group.all")}</option>
          {model.groups.map((group) => <option key={group.id} value={group.id}>{group.label}</option>)}
        </select>
        <select className="graph-explorer-select" value={labelMode} onChange={(event) => setLabelMode(event.target.value as LabelMode)}>
          <option value="important">{t(language, "graph.labels.important")}</option>
          <option value="all">{t(language, "graph.labels.all")}</option>
          <option value="off">{t(language, "graph.labels.off")}</option>
        </select>
        <Button variant="outline" onClick={() => setFitSignal((value) => value + 1)}>
          <LocateFixed className="h-4 w-4" /> {t(language, "graph.fit")}
        </Button>
      </div>

      <div className="graph-explorer-stage">
        <div className="graph-explorer-canvas-col">
          <CytoscapeGraph
            model={model}
            selectedId={selectedId}
            onSelect={setSelectedId}
            fitSignal={fitSignal}
            ariaLabel={t(language, "graph.canvas.aria")}
          />

          <div className="graph-explorer-meta">
            <div className="graph-explorer-meta-stats">
              <span>{t(language, "graph.deep.summary", { nodes: fmtNumber(model.visibleNodes.length), edges: fmtNumber(model.visibleEdges.length), total: fmtNumber(model.totalNodes) })}</span>
              <Badge variant={model.hiddenByFilters ? "warning" : "success"}>
                {model.hiddenByFilters ? t(language, "graph.filtered", { count: fmtNumber(model.hiddenByFilters) }) : t(language, "graph.allInView")}
              </Badge>
            </div>
            <div className="graph-explorer-meta-tools">
              <Filter className="h-3.5 w-3.5" />
              <label htmlFor="importance">{t(language, "graph.importance")}</label>
              <input
                id="importance"
                type="range"
                min="0"
                max="0.9"
                step="0.05"
                value={minImportance}
                onChange={(event) => setMinImportance(Number(event.target.value))}
                className="graph-explorer-range"
                aria-label={t(language, "graph.minImportance.aria")}
              />
              <Badge variant="muted">{Math.round(minImportance * 100)}%+</Badge>
              {selectedId ? <Button variant="outline" size="sm" onClick={() => setSelectedId(null)}>{t(language, "graph.clearFocus")}</Button> : null}
              {search.trim() ? <Button variant="outline" size="sm" onClick={() => backendSearch.mutate()} disabled={backendSearch.isPending}>{t(language, "graph.searchAll")}</Button> : null}
            </div>
          </div>

          <div className="graph-explorer-groups">
            {model.groups.map((group) => (
              <button
                key={group.id}
                type="button"
                onClick={() => toggleGroup(group.id)}
                className="graph-explorer-chip"
              >
                <span className="graph-explorer-chip-swatch" style={{ backgroundColor: group.color }} />
                <span>{group.label}</span>
                <Badge variant={group.collapsed ? "warning" : "muted"}>{group.collapsed ? t(language, "graph.collapsed") : fmtNumber(group.count)}</Badge>
              </button>
            ))}
          </div>
        </div>

        <aside className="graph-explorer-aside">
          {selectedId ? (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Focus className="h-4 w-4" /> {t(language, "graph.focus.title")}</CardTitle>
                <CardDescription>{t(language, "graph.focus.desc")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {selected ? (
                  <>
                    <div>
                      <div className="text-lg font-semibold">{selected.label}</div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        <Badge variant="muted">{graphTypeLabel(selected.type, language)}</Badge>
                        <Badge variant="muted">{groupLabelFor(selected, model.groups)}</Badge>
                        <Badge variant="success">{t(language, "graph.importanceBadge", { n: Math.round(selected.importance * 100) })}</Badge>
                      </div>
                    </div>
                    {selected.summary ? <p className="text-sm text-muted-foreground">{selected.summary}</p> : null}
                    {mode === "basic" ? (
                      <KeyValueList data={{
                        connections: selected.degree,
                        source: selected.source || t(language, "graph.source.none"),
                        source_type: sourceType(selected.raw, language),
                        created_at: sourceCreatedAt(selected.raw) || t(language, "graph.created.none"),
                      }} />
                    ) : (
                      <StructuredView value={{
                        id: selected.id,
                        degree: selected.degree,
                        source: selected.source || t(language, "graph.source.none"),
                        source_type: sourceType(selected.raw, language),
                        created_at: sourceCreatedAt(selected.raw) || t(language, "graph.created.none"),
                      }} />
                    )}
                    {selected.source ? <Button variant="outline" size="sm" onClick={() => void navigator.clipboard?.writeText(selected.source)}>{t(language, "graph.copySource")}</Button> : null}
                  </>
                ) : selectedGroup ? (
                  <div className="space-y-2">
                    <Badge variant="warning">{t(language, "graph.collapsedGroup")}</Badge>
                    <div className="text-lg font-semibold">{selectedGroup.label}</div>
                    <Button variant="outline" onClick={() => toggleGroup(selectedGroup.id)}>{t(language, "graph.expandGroup")}</Button>
                  </div>
                ) : <EmptyState title={t(language, "graph.nothingSelected.title")} detail={t(language, "graph.nothingSelected.detail")} />}
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4" /> {t(language, "graph.important.title")}</CardTitle>
                <CardDescription>{t(language, "graph.important.desc")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {model.visibleNodes.slice(0, 8).map((node) => (
                  <button
                    key={node.id}
                    type="button"
                    onClick={() => setSelectedId(node.id)}
                    className="graph-explorer-pick"
                  >
                    <div className="graph-explorer-pick-copy">
                      <div className="graph-explorer-pick-title">{node.label}</div>
                      <div className="graph-explorer-pick-meta">{graphTypeLabel(node.type, language)}</div>
                    </div>
                    <Badge variant="muted">{Math.round(node.importance * 100)}%</Badge>
                  </button>
                ))}
              </CardContent>
            </Card>
          )}
        </aside>
      </div>
      {backendSearch.data ? (
        <DataPanel title={t(language, "brain.search.results")} result={backendSearch.data}>
          {(result) => <EntityList items={(result as Record<string, unknown>).matches || result} titleKey="title" metaKey="type" limit={8} />}
        </DataPanel>
      ) : null}
    </div>
  );
}
