import * as React from "react";
import "@/i18n/brain";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Box, Eye, EyeOff, Filter, Focus, LocateFixed, Network, Search, Sparkles, X } from "lucide-react";
import { latticeApi } from "@/api/client";
import { DataPanel, EmptyState, EntityList } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAppStore } from "@/store/appStore";
import { t } from "@/i18n";
import { fmtNumber } from "@/lib/utils";
import { CytoscapeGraph } from "./CytoscapeGraph";
import { GraphNodeInspector } from "./GraphNodeInspector";
import { buildExplorerModel, nodeDisplayLabels, parseGraph, type LabelMode } from "./graphExplorer";
import { graphTypeLabel } from "./sourceMeta";

const Brain3DCanvas = React.lazy(() =>
  import("@/features/brain3d/Brain3DCanvas").then((module) => ({ default: module.Brain3DCanvas })),
);

export function DigitalBrainExplorer({ data }: { data: unknown }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const theme = useAppStore((state) => state.theme);
  const focusedGraphNodeId = useAppStore((state) => state.focusedGraphNodeId);
  const setFocusedGraphNodeId = useAppStore((state) => state.setFocusedGraphNodeId);
  const graphViewMode = useAppStore((state) => state.graphViewMode);
  const setGraphViewMode = useAppStore((state) => state.setGraphViewMode);
  const showSuperseded = useAppStore((state) => state.showSuperseded);
  const setShowSuperseded = useAppStore((state) => state.setShowSuperseded);
  const activeFiringTrace = useAppStore((state) => state.activeFiringTrace);
  const parsed = React.useMemo(() => parseGraph(data, language), [data, language, theme]);
  const [search, setSearch] = React.useState("");
  const [groupFilter, setGroupFilter] = React.useState("all");
  const [conflictsOnly, setConflictsOnly] = React.useState(false);
  const [minImportance, setMinImportance] = React.useState(mode === "basic" ? 0.1 : 0);
  const [labelMode, setLabelMode] = React.useState<LabelMode>("important");
  const [collapsedGroups, setCollapsedGroups] = React.useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [fitSignal, setFitSignal] = React.useState(0);
  const searchInputRef = React.useRef<HTMLInputElement>(null);
  const backendSearch = useMutation({ mutationFn: () => latticeApi.hybridSearch(search.trim()) });

  React.useEffect(() => {
    if (!focusedGraphNodeId || !parsed.nodes.length) return;
    if (parsed.nodes.some((node) => node.id === focusedGraphNodeId)) {
      setSelectedId(focusedGraphNodeId);
      setFitSignal((value) => value + 1);
    }
    setFocusedGraphNodeId(null);
  }, [focusedGraphNodeId, parsed.nodes, setFocusedGraphNodeId]);

  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const inField = Boolean(target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT"));
      if (event.key === "/" && !inField && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        searchInputRef.current?.focus();
      } else if (event.key === "Escape" && (selectedId || conflictsOnly)) {
        setSelectedId(null);
        setConflictsOnly(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedId, conflictsOnly]);

  const model = React.useMemo(() => buildExplorerModel({
    graph: parsed,
    search,
    groupFilter,
    minImportance,
    collapsedGroups,
    selectedId,
    labelMode,
    maxNodes: 220,
    conflictsOnly,
    showSuperseded,
  }), [parsed, search, groupFilter, minImportance, collapsedGroups, selectedId, labelMode, conflictsOnly, showSuperseded]);

  const labels = React.useMemo(() => nodeDisplayLabels(model), [model]);

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
            ref={searchInputRef}
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
        {parsed.conflictNodeIds.size > 0 ? (
          <Button
            type="button"
            variant={conflictsOnly ? "default" : "outline"}
            aria-pressed={conflictsOnly}
            data-testid="graph-conflicts-toggle"
            onClick={() => setConflictsOnly((prev) => !prev)}
          >
            <AlertTriangle className="h-4 w-4" />
            {t(language, "graph.conflictsOnly", { count: fmtNumber(parsed.conflictNodeIds.size) })}
          </Button>
        ) : null}
        <Button
          type="button"
          variant={graphViewMode === "3d" ? "default" : "outline"}
          aria-pressed={graphViewMode === "3d"}
          aria-label={t(language, "graph.view.toggleAria")}
          data-testid="graph-view-toggle"
          onClick={() => setGraphViewMode(graphViewMode === "3d" ? "2d" : "3d")}
        >
          {graphViewMode === "3d" ? <Box className="h-4 w-4" aria-hidden="true" /> : <Network className="h-4 w-4" aria-hidden="true" />}
          {t(language, graphViewMode === "3d" ? "graph.view.3d" : "graph.view.2d")}
        </Button>
        <Button
          type="button"
          variant={showSuperseded ? "outline" : "default"}
          aria-pressed={!showSuperseded}
          data-testid="graph-superseded-toggle"
          onClick={() => setShowSuperseded(!showSuperseded)}
        >
          {showSuperseded ? <Eye className="h-4 w-4" aria-hidden="true" /> : <EyeOff className="h-4 w-4" aria-hidden="true" />}
          {t(language, showSuperseded ? "graph.superseded.show" : "graph.superseded.hide")}
        </Button>
        <Button variant="outline" onClick={() => setFitSignal((value) => value + 1)}>
          <LocateFixed className="h-4 w-4" /> {t(language, "graph.fit")}
        </Button>
      </div>

      <div className="graph-explorer-stage" data-testid="brain-graph-stage">
        <div className="graph-explorer-canvas-col">
          {graphViewMode === "3d" ? (
            <React.Suspense
              fallback={
                <CytoscapeGraph
                  model={model}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  fitSignal={fitSignal}
                  ariaLabel={t(language, "graph.canvas.aria")}
                />
              }
            >
              <Brain3DCanvas
                nodes={model.visibleNodes}
                labels={labels}
                edges={model.visibleEdges}
                selectedId={selectedId}
                onSelectNode={setSelectedId}
                showSuperseded={showSuperseded}
                firingTrace={activeFiringTrace}
                resetSignal={fitSignal}
                variant="explorer"
                language={language}
                fallback={
                  <CytoscapeGraph
                    model={model}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    fitSignal={fitSignal}
                    ariaLabel={t(language, "graph.canvas.aria")}
                  />
                }
              />
            </React.Suspense>
          ) : (
            <CytoscapeGraph
              model={model}
              selectedId={selectedId}
              onSelect={setSelectedId}
              fitSignal={fitSignal}
              ariaLabel={t(language, "graph.canvas.aria")}
            />
          )}

          <div className="graph-explorer-meta">
            <div className="graph-explorer-meta-stats">
              <span>{t(language, "graph.deep.summary", { nodes: fmtNumber(model.visibleNodes.length), edges: fmtNumber(model.visibleEdges.length), total: fmtNumber(model.totalNodes) })}</span>
              <Badge variant={model.hiddenByFilters ? "warning" : "success"}>
                {model.hiddenByFilters ? t(language, "graph.filtered", { count: fmtNumber(model.hiddenByFilters) }) : t(language, "graph.allInView")}
              </Badge>
              <Badge variant="muted">{t(language, "graph.shortcutHint")}</Badge>
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

        <aside
          className={`graph-explorer-aside${selectedId ? " is-docked-open" : ""}`}
          data-testid="graph-inspector-drawer"
        >
          {selectedId ? (
            <Card className="graph-inspector-card">
              <CardHeader className="graph-inspector-head">
                <div className="graph-inspector-head-row">
                  <CardTitle className="flex items-center gap-2"><Focus className="h-4 w-4" /> {t(language, "graph.focus.title")}</CardTitle>
                  <button
                    type="button"
                    className="graph-inspector-close"
                    data-testid="graph-inspector-close"
                    aria-label={t(language, "graph.inspector.close")}
                    onClick={() => setSelectedId(null)}
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
                <CardDescription>{t(language, "graph.focus.desc")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {selected ? (
                  <GraphNodeInspector
                    selected={selected}
                    groups={model.groups}
                    mode={mode}
                    language={language}
                    onSelectNode={setSelectedId}
                  />
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
