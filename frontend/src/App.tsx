import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ChevronDown, DatabaseBackup, Download, Eye, ImagePlus, RotateCcw, Search, Send, ShieldCheck } from "lucide-react";
import { latticeApi, type ApiResult } from "@/api/client";
import { Button } from "@/components/ui/button";
import { type BrainState, LivingBrain, triggerBrainRecall } from "@/components/LivingBrain";
import { ProductFlow, readProductFlowComplete } from "@/components/ProductFlow";
import { useAppStore } from "@/store/appStore";
import { asArray } from "@/lib/utils";

type ApiRecord = Record<string, unknown>;
type BrainDepth = 1 | 2 | 3 | 4 | 5;

type Message = {
  role: "user" | "assistant";
  content: string;
};

type MemoryFragment = {
  id: string;
  title: string;
  kind: string;
};

type KnowledgeConcept = {
  id: string;
  label: string;
  type: string;
  summary: string;
  importance: number;
};

type RelationshipThread = {
  id: string;
  source: string;
  target: string;
  label: string;
  weight: number;
};

type KnowledgeGraphModel = {
  nodes: KnowledgeConcept[];
  edges: RelationshipThread[];
};

const DEPTHS: Array<{ level: BrainDepth; label: string; state: BrainState }> = [
  { level: 1, label: "Living Brain", state: "idle" },
  { level: 2, label: "Memory Layer", state: "recalling" },
  { level: 3, label: "Knowledge Layer", state: "synthesizing" },
  { level: 4, label: "Relationship Layer", state: "planning" },
  { level: 5, label: "Knowledge Graph", state: "synthesizing" },
];

const STARTER_PROMPTS = [
  "Remember this decision: ",
  "What do I already know about ",
  "Help me turn this project context into a plan: ",
];

export default function App() {
  const theme = useAppStore((state) => state.theme);
  const [flowComplete, setFlowComplete] = React.useState(readProductFlowComplete);
  const { state: brainState, intensity, setBrain } = useBrainState();

  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.querySelector<HTMLTextAreaElement>(".brain-composer textarea")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!flowComplete) {
    return <ProductFlow onComplete={() => setFlowComplete(true)} />;
  }

  return (
    <div className="brain-space">
      <div className="brain-field" />
      <BrainHome brainState={brainState} intensity={intensity} onBrainChange={setBrain} />
    </div>
  );
}

function useBrainState() {
  const [state, setState] = React.useState<BrainState>("idle");
  const [intensity, setIntensity] = React.useState(0.58);

  const setBrain = React.useCallback((next: BrainState, nextIntensity?: number) => {
    setState(next);
    if (nextIntensity !== undefined) setIntensity(clamp(nextIntensity, 0.38, 1));
  }, []);

  return { state, intensity, setBrain };
}

function BrainHome({
  brainState,
  intensity,
  onBrainChange,
}: {
  brainState: BrainState;
  intensity: number;
  onBrainChange: (state: BrainState, intensity?: number) => void;
}) {
  const qc = useQueryClient();
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [draft, setDraft] = React.useState("");
  const [imageData, setImageData] = React.useState<string | null>(null);
  const [streaming, setStreaming] = React.useState(false);
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const [explorationDepth, setExplorationDepth] = React.useState<BrainDepth>(1);
  const [graphSearch, setGraphSearch] = React.useState("");
  const [selectedGraphId, setSelectedGraphId] = React.useState<string | null>(null);
  const streamRef = React.useRef<HTMLDivElement>(null);
  const recallTimerRef = React.useRef<number | null>(null);

  const memoriesQ = useQuery({ queryKey: ["memoryManager"], queryFn: latticeApi.memoryManager });
  const historyQ = useQuery({ queryKey: ["chatHistory"], queryFn: latticeApi.chatHistory });
  const graphQ = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph });
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });

  const memoryFragments = React.useMemo(
    () => buildMemoryFragments(memoriesQ.data?.data, historyQ.data?.data),
    [memoriesQ.data, historyQ.data],
  );
  const graphModel = React.useMemo(() => parseKnowledgeGraph(graphQ.data?.data), [graphQ.data]);
  const knowledgeConcepts = React.useMemo(
    () => graphModel.nodes.slice(0, 10),
    [graphModel.nodes],
  );
  const relationshipThreads = React.useMemo(
    () => graphModel.edges.slice(0, 10),
    [graphModel.edges],
  );
  const modelName = React.useMemo(() => currentModelName(modelsQ.data?.data), [modelsQ.data]);
  const currentDepth = DEPTHS[explorationDepth - 1];

  React.useEffect(() => {
    if (streaming) onBrainChange("thinking", 0.94);
    else if (draft.trim().length > 4) onBrainChange("listening", 0.76);
    else onBrainChange(currentDepth.state, explorationDepth === 1 ? 0.58 : 0.66 + explorationDepth * 0.06);
  }, [streaming, draft, currentDepth.state, explorationDepth, onBrainChange]);

  React.useEffect(() => {
    const stream = streamRef.current;
    if (stream) stream.scrollTop = stream.scrollHeight;
  }, [messages]);

  React.useEffect(() => {
    return () => {
      if (recallTimerRef.current !== null) window.clearTimeout(recallTimerRef.current);
    };
  }, []);

  async function send() {
    const text = draft.trim();
    if (!text || streaming) return;
    const activeConversationId = conversationId || `brain-${Date.now()}`;
    if (!conversationId) setConversationId(activeConversationId);

    setMessages((items) => [...items, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setDraft("");
    setImageData(null);
    setStreaming(true);
    onBrainChange("thinking", 0.96);

    try {
      const result = await latticeApi.streamChat(
        { message: text, conversation_id: activeConversationId, image_data: imageData || undefined },
        {
          onChunk: (_delta, fullText) => {
            setMessages((items) => {
              const next = [...items];
              next[next.length - 1] = { role: "assistant", content: fullText };
              return next;
            });
          },
          onTrace: (trace) => {
            if (!trace) return;
            onBrainChange("recalling", 0.9);
            triggerBrainRecall();
            if (recallTimerRef.current !== null) window.clearTimeout(recallTimerRef.current);
            recallTimerRef.current = window.setTimeout(() => onBrainChange("thinking", 0.9), 900);
          },
        },
      );
      if (result.error) {
        setMessages((items) => {
          const next = [...items];
          next[next.length - 1] = { role: "assistant", content: `Unavailable: ${result.error}` };
          return next;
        });
      }
    } finally {
      setStreaming(false);
      void qc.invalidateQueries({ queryKey: ["chatHistory"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
    }
  }

  function deepen() {
    setExplorationDepth((depth) => {
      const next = Math.min(5, depth + 1) as BrainDepth;
      const nextDepth = DEPTHS[next - 1];
      onBrainChange(nextDepth.state, 0.66 + next * 0.06);
      if (next >= 2) triggerBrainRecall();
      return next;
    });
  }

  function surface() {
    setExplorationDepth(1);
    setSelectedGraphId(null);
    setGraphSearch("");
    onBrainChange("idle", 0.58);
  }

  function recallMemory(fragment: MemoryFragment) {
    triggerBrainRecall();
    setExplorationDepth((depth) => Math.max(depth, 2) as BrainDepth);
    setMessages((items) => [
      ...items,
      { role: "assistant", content: `I am recalling ${fragment.kind.toLowerCase()}: ${fragment.title}` },
    ]);
  }

  return (
    <main className="brain-home" aria-label="Lattice Brain">
      <section className="brain-presence" aria-label="Brain exploration">
        <div className="brain-exploration" data-depth={explorationDepth}>
          <LivingBrain
            state={brainState}
            intensity={intensity + explorationDepth * 0.035}
            size="large"
            depth={explorationDepth}
            showLabel={false}
            onInteract={deepen}
          />

          <div className="brain-depth-badge" aria-live="polite">
            <span>Level {explorationDepth}</span>
            <strong>{currentDepth.label}</strong>
          </div>

          <div className="brain-field-layer" aria-hidden={explorationDepth < 2}>
            <DepthEmergence
              depth={explorationDepth}
              memories={memoryFragments}
              concepts={knowledgeConcepts}
              relationships={relationshipThreads}
              graphModel={graphModel}
              graphSearch={graphSearch}
              selectedGraphId={selectedGraphId}
              onGraphSearch={setGraphSearch}
              onSelectGraphNode={setSelectedGraphId}
              onRecallMemory={recallMemory}
            />
          </div>

          {explorationDepth > 1 ? (
            <button className="brain-surface-control" type="button" onClick={surface}>
              Surface
            </button>
          ) : null}
        </div>
      </section>

      <section className="brain-conversation" aria-label="Conversation">
        <div className="brain-conversation-header">
          <div>
            <h1>Lattice Brain</h1>
            <span>{currentDepth.label}</span>
          </div>
          <div className="brain-ownership-strip" aria-label="Brain ownership guarantees">
            <span>Local-first</span>
            <span>Portable</span>
            <span>Private</span>
          </div>
          <div>{modelName}</div>
        </div>

        <div ref={streamRef} className="brain-stream">
          {messages.length === 0 ? (
            <div className="mind-empty">
              <div className="mind-empty-kicker">Your durable context</div>
              <div className="mind-empty-title">Start with what should not be forgotten.</div>
              <p>
                Lattice keeps your documents, conversations, projects, and decisions available while models can change around them.
              </p>
              <div className="mind-empty-prompts" aria-label="Starter prompts">
                {STARTER_PROMPTS.map((prompt) => (
                  <button key={prompt} type="button" onClick={() => setDraft(prompt)}>
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`brain-message ${message.role}`}>
                <div className="brain-message-bubble">{message.content}</div>
              </div>
            ))
          )}
        </div>

        <BrainCarePanel />

        <div className="brain-composer">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            placeholder="Talk to your Brain..."
          />
          <div className="brain-composer-actions">
            <label className="brain-image-input">
              <ImagePlus className="h-3.5 w-3.5" />
              <span>Image</span>
              <input
                type="file"
                accept="image/*"
                className="sr-only"
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (file) setImageData(await fileToDataUrl(file));
                }}
              />
            </label>
            {imageData ? <span className="brain-quiet-success">Image attached</span> : null}
            <Button onClick={() => void send()} disabled={!draft.trim() || streaming} className="rounded-full px-5">
              <Send className="h-4 w-4" /> Send
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}

function BrainCarePanel() {
  const qc = useQueryClient();
  const [expanded, setExpanded] = React.useState(false);
  const [archivePath, setArchivePath] = React.useState("");
  const [passphrase, setPassphrase] = React.useState("");
  const [latestResult, setLatestResult] = React.useState<ApiResult | null>(null);
  const portabilityQ = useQuery({ queryKey: ["portability"], queryFn: latticeApi.graphPortability });
  const backupHealthQ = useQuery({ queryKey: ["backupHealth"], queryFn: latticeApi.backupHealth });
  const rememberResult = React.useCallback((result: ApiResult) => setLatestResult(result), []);

  const exportGraph = useCareMutation(() => latticeApi.graphExport(), undefined, rememberResult);
  const backupGraph = useCareMutation(() => latticeApi.graphBackup(), () => {
    void qc.invalidateQueries({ queryKey: ["backupHealth"] });
    void qc.invalidateQueries({ queryKey: ["portability"] });
  }, rememberResult);
  const archiveBrain = useCareMutation(
    () => latticeApi.brainArchive({ path: archivePath.trim() || null, passphrase }),
    () => void qc.invalidateQueries({ queryKey: ["backupHealth"] }),
    rememberResult,
  );
  const inspectArchive = useCareMutation(() => latticeApi.brainArchiveInspect({
    path: archivePath.trim(),
    passphrase: passphrase || null,
  }), undefined, rememberResult);
  const restorePreview = useCareMutation(() => latticeApi.brainArchiveRestore({
    path: archivePath.trim(),
    passphrase,
    dry_run: true,
    confirm: false,
  }), undefined, rememberResult);

  const portableFormat = portabilityLabel(portabilityQ.data?.data);
  const backupStatus = backupHealthLabel(backupHealthQ.data?.data);

  return (
    <section className={`brain-care-panel ${expanded ? "is-expanded" : "is-collapsed"}`} aria-label="Care for my Brain">
      <button
        className="brain-care-summary"
        type="button"
        aria-expanded={expanded}
        aria-controls="brain-care-details"
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="brain-care-summary-main">
          <span><ShieldCheck className="h-3.5 w-3.5" /> Care for my Brain</span>
          <strong>Own it locally. Keep it portable.</strong>
        </span>
        <div className="brain-care-proof" aria-label="Ownership model">
          <span>Private</span>
          <span>{portableFormat}</span>
          <span>{backupStatus}</span>
        </div>
        <ChevronDown className="brain-care-toggle h-4 w-4" aria-hidden="true" />
      </button>

      {expanded ? (
        <div id="brain-care-details" className="brain-care-details">
          <div className="brain-care-actions">
            <CareButton
              icon={<Download className="h-3.5 w-3.5" />}
              label="Export"
              detail="Take it with you"
              pending={exportGraph.isPending}
              onClick={() => exportGraph.mutate()}
            />
            <CareButton
              icon={<DatabaseBackup className="h-3.5 w-3.5" />}
              label="Backup"
              detail="Save a copy"
              pending={backupGraph.isPending}
              onClick={() => backupGraph.mutate()}
            />
            <CareButton
              icon={<Archive className="h-3.5 w-3.5" />}
              label="Archive"
              detail="Encrypted Brain"
              pending={archiveBrain.isPending}
              disabled={!passphrase.trim()}
              onClick={() => archiveBrain.mutate()}
            />
          </div>

          <div className="brain-care-archive">
            <input
              value={archivePath}
              onChange={(event) => setArchivePath(event.target.value)}
              placeholder="Paste an archive path to inspect or preview"
              aria-label="Brain archive path"
            />
            <input
              type="password"
              value={passphrase}
              onChange={(event) => setPassphrase(event.target.value)}
              placeholder="Archive passphrase"
              aria-label="Brain archive passphrase"
            />
            <div className="brain-care-archive-actions">
              <Button
                variant="outline"
                size="sm"
                disabled={!archivePath.trim() || inspectArchive.isPending}
                onClick={() => inspectArchive.mutate()}
              >
                <Eye className="h-3.5 w-3.5" /> Inspect
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!archivePath.trim() || !passphrase.trim() || restorePreview.isPending}
                onClick={() => restorePreview.mutate()}
              >
                <RotateCcw className="h-3.5 w-3.5" /> Restore preview
              </Button>
            </div>
          </div>

          {latestResult ? (
            <div className={`brain-care-result ${latestResult.ok ? "is-ok" : "is-error"}`} role="status">
              {summarizeCareResult(latestResult)}
            </div>
          ) : (
            <p className="brain-care-note">
              Restore preview checks an archive without changing your Brain. Confirmed restore stays in Settings.
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}

function useCareMutation<T extends ApiResult>(
  mutationFn: () => Promise<T>,
  onSuccess?: () => void,
  onResult?: (result: T) => void,
) {
  return useMutation({
    mutationFn,
    onSuccess: (result) => {
      onResult?.(result);
      onSuccess?.();
    },
  });
}

function CareButton({
  icon,
  label,
  detail,
  pending,
  disabled,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  detail: string;
  pending?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button className="brain-care-button" type="button" disabled={disabled || pending} onClick={onClick}>
      {icon}
      <span>
        <strong>{pending ? "Working" : label}</strong>
        <small>{detail}</small>
      </span>
    </button>
  );
}

function DepthEmergence({
  depth,
  memories,
  concepts,
  relationships,
  graphModel,
  graphSearch,
  selectedGraphId,
  onGraphSearch,
  onSelectGraphNode,
  onRecallMemory,
}: {
  depth: BrainDepth;
  memories: MemoryFragment[];
  concepts: KnowledgeConcept[];
  relationships: RelationshipThread[];
  graphModel: KnowledgeGraphModel;
  graphSearch: string;
  selectedGraphId: string | null;
  onGraphSearch: (value: string) => void;
  onSelectGraphNode: (id: string | null) => void;
  onRecallMemory: (fragment: MemoryFragment) => void;
}) {
  if (depth === 1) return null;

  return (
    <>
      {depth >= 2 ? (
        <MemoryLayer memories={memories} depth={depth} onRecallMemory={onRecallMemory} />
      ) : null}
      {depth >= 3 && depth < 5 ? (
        <KnowledgeLayer concepts={concepts} depth={depth} />
      ) : null}
      {depth >= 4 && depth < 5 ? (
        <RelationshipLayer concepts={concepts} relationships={relationships} />
      ) : null}
      {depth >= 5 ? (
        <EmergentKnowledgeGraph
          model={graphModel}
          search={graphSearch}
          selectedId={selectedGraphId}
          onSearch={onGraphSearch}
          onSelect={onSelectGraphNode}
        />
      ) : null}
    </>
  );
}

function MemoryLayer({
  memories,
  depth,
  onRecallMemory,
}: {
  memories: MemoryFragment[];
  depth: BrainDepth;
  onRecallMemory: (fragment: MemoryFragment) => void;
}) {
  const visible = memories.slice(0, depth >= 3 ? 8 : 6);
  if (!visible.length) return <div className="memory-fragment is-empty">Memory is quiet</div>;

  return (
    <>
      {visible.map((memory, index) => {
        const point = polarPoint(index, visible.length, depth >= 3 ? 39 : 31, depth >= 3 ? 24 : 18, -112);
        return (
          <button
            key={memory.id}
            type="button"
            className="memory-fragment"
            style={layerStyle({ "--x": `${point.x}%`, "--y": `${point.y}%`, "--delay": `${index * 55}ms` })}
            onClick={() => onRecallMemory(memory)}
          >
            <span>{memory.kind}</span>
            <strong>{memory.title}</strong>
          </button>
        );
      })}
    </>
  );
}

function KnowledgeLayer({ concepts, depth }: { concepts: KnowledgeConcept[]; depth: BrainDepth }) {
  const visible = concepts.slice(0, depth >= 4 ? 10 : 7);
  if (!visible.length) return <div className="concept-signal is-empty">Knowledge is forming</div>;

  return (
    <>
      {visible.map((concept, index) => {
        const point = polarPoint(index, visible.length, 24, 15, -70);
        return (
          <button
            key={concept.id}
            type="button"
            className="concept-signal"
            style={layerStyle({ "--x": `${point.x}%`, "--y": `${point.y}%`, "--delay": `${index * 45}ms` })}
            title={concept.summary || concept.type}
          >
            <span>{concept.type}</span>
            {concept.label}
          </button>
        );
      })}
    </>
  );
}

function RelationshipLayer({
  concepts,
  relationships,
}: {
  concepts: KnowledgeConcept[];
  relationships: RelationshipThread[];
}) {
  const visibleConcepts = concepts.slice(0, 10);
  const layout = layoutGraphNodes(visibleConcepts, 30, 20);
  const positionById = new Map(layout.map((item) => [item.node.id, item]));
  const visibleRelationships = relationships
    .map((relationship, index) => {
      const source = positionById.get(relationship.source) || layout[index % Math.max(layout.length, 1)];
      const target = positionById.get(relationship.target) || layout[(index + 3) % Math.max(layout.length, 1)];
      return source && target && source.node.id !== target.node.id ? { relationship, source, target } : null;
    })
    .filter(Boolean)
    .slice(0, 8) as Array<{
      relationship: RelationshipThread;
      source: ReturnType<typeof layoutGraphNodes>[number];
      target: ReturnType<typeof layoutGraphNodes>[number];
    }>;

  if (!visibleRelationships.length) return null;

  return (
    <svg className="relationship-weave" viewBox="0 0 100 100" aria-hidden>
      {visibleRelationships.map(({ relationship, source, target }, index) => (
        <line
          key={`${relationship.id}-${index}`}
          x1={source.x}
          y1={source.y}
          x2={target.x}
          y2={target.y}
          style={{ animationDelay: `${index * 80}ms` }}
        />
      ))}
    </svg>
  );
}

function EmergentKnowledgeGraph({
  model,
  search,
  selectedId,
  onSearch,
  onSelect,
}: {
  model: KnowledgeGraphModel;
  search: string;
  selectedId: string | null;
  onSearch: (value: string) => void;
  onSelect: (id: string | null) => void;
}) {
  const query = search.trim().toLowerCase();
  const visibleNodes = React.useMemo(() => {
    const filtered = model.nodes.filter((node) => {
      if (!query) return true;
      return `${node.label} ${node.type} ${node.summary}`.toLowerCase().includes(query);
    });
    return filtered.slice(0, 18);
  }, [model.nodes, query]);
  const layout = React.useMemo(() => layoutGraphNodes(visibleNodes, 38, 24), [visibleNodes]);
  const positionById = React.useMemo(() => new Map(layout.map((item) => [item.node.id, item])), [layout]);
  const visibleEdges = React.useMemo(
    () => model.edges.filter((edge) => positionById.has(edge.source) && positionById.has(edge.target)).slice(0, 36),
    [model.edges, positionById],
  );
  const selected = visibleNodes.find((node) => node.id === selectedId) || visibleNodes[0] || null;

  return (
    <section className="mind-core-graph" data-testid="emergent-knowledge-graph" aria-label="Knowledge Graph">
      <div className="brain-graph-head">
        <div>
          <span>Level 5</span>
          <strong>Knowledge Graph</strong>
        </div>
        <label className="brain-graph-search">
          <Search className="h-3.5 w-3.5" />
          <input
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder="Search"
            aria-label="Search knowledge graph"
          />
        </label>
      </div>

      {visibleNodes.length ? (
        <div className="brain-graph-canvas">
          <svg className="brain-graph-edges" viewBox="0 0 100 100" aria-hidden>
            {visibleEdges.map((edge, index) => {
              const source = positionById.get(edge.source);
              const target = positionById.get(edge.target);
              if (!source || !target) return null;
              return (
                <line
                  key={`${edge.id}-${index}`}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  style={{ "--weight": String(clamp(edge.weight, 0.4, 2.8)) } as React.CSSProperties}
                />
              );
            })}
          </svg>
          {layout.map(({ node, x, y }, index) => (
            <button
              key={node.id}
              type="button"
              className={`graph-node ${selected?.id === node.id ? "is-selected" : ""}`}
              style={layerStyle({ "--x": `${x}%`, "--y": `${y}%`, "--delay": `${index * 35}ms` })}
              onClick={() => onSelect(node.id)}
            >
              <span>{node.type}</span>
              {node.label}
            </button>
          ))}
        </div>
      ) : (
        <div className="brain-graph-empty">No matching knowledge yet</div>
      )}

      <div className="brain-graph-focus">
        {selected ? (
          <>
            <span>{selected.type}</span>
            <strong>{selected.label}</strong>
            <p>{selected.summary || "This concept is part of the deepest knowledge layer."}</p>
          </>
        ) : (
          <p>Capture documents, conversations, or projects to grow the graph.</p>
        )}
      </div>
    </section>
  );
}

function buildMemoryFragments(memoryData: unknown, historyData: unknown): MemoryFragment[] {
  const memory = isRecord(memoryData) ? memoryData : {};
  const sourceRows = asArray<ApiRecord>(memory.sources).length
    ? asArray<ApiRecord>(memory.sources)
    : asArray<ApiRecord>(memory.tiers);
  const sourceFragments = sourceRows.map((item, index) => ({
    id: textValue(item, ["id", "source", "label"], `memory-${index}`),
    title: textValue(item, ["title", "label", "source", "path", "name"], "Workspace memory"),
    kind: titleValue(item, ["type", "source_type", "kind", "health"], "Memory"),
  }));
  const conversationFragments = asArray<ApiRecord>(historyData).map((item, index) => ({
    id: textValue(item, ["id", "conversation_id"], `conversation-${index}`),
    title: textValue(item, ["title", "summary", "id"], "Conversation"),
    kind: "Conversation",
  }));

  return uniqueById([...sourceFragments, ...conversationFragments]).slice(0, 10);
}

function parseKnowledgeGraph(data: unknown): KnowledgeGraphModel {
  const graph = isRecord(data) ? data : {};
  const rawNodes = asArray<ApiRecord>(graph.nodes);
  const rawEdges = asArray<ApiRecord>(graph.edges);
  const nodes = rawNodes.flatMap((node): KnowledgeConcept[] => {
    const id = textValue(node, ["id", "node_id", "title", "label"]);
    if (!id) return [];
    const metadata = isRecord(node.metadata) ? node.metadata : {};
    const type = titleValue(node, ["type", "kind", "category"], "Concept");
    const label = textValue(node, ["title", "label", "name"], id.replace(/^[^:]+:/, ""));
    const summary = textValue(node, ["summary", "description", "snippet"]) || textValue(metadata, ["summary", "description", "relative_path", "filename"]);
    const importance = clamp(numberValue(node, ["importance_norm", "importance", "score"]) || 0.5, 0.08, 1);
    return [{ id, label, type, summary, importance }];
  }).sort((left, right) => right.importance - left.importance);
  const ids = new Set(nodes.map((node) => node.id));
  const edges = rawEdges.flatMap((edge, index): RelationshipThread[] => {
    const source = textValue(edge, ["from", "source", "source_id"]);
    const target = textValue(edge, ["to", "target", "target_id"]);
    if (!source || !target || !ids.has(source) || !ids.has(target)) return [];
    return [{
      id: textValue(edge, ["id"], `edge-${index}`),
      source,
      target,
      label: titleValue(edge, ["type", "label", "relationship"], "Relates"),
      weight: numberValue(edge, ["weight", "score", "confidence"]) || 1,
    }];
  });
  return { nodes, edges };
}

function currentModelName(data: unknown) {
  const record = isRecord(data) ? data : {};
  const current = textValue(record, ["current", "current_model", "local_model"]);
  if (current) return current;
  const loaded = asArray<ApiRecord>(record.loaded || record.loaded_models);
  const firstLoaded = loaded.find((item) => item.id || item.name || item.model_id);
  return firstLoaded ? textValue(firstLoaded, ["name", "id", "model_id"], "local mind") : "local mind";
}

function portabilityLabel(data: unknown) {
  const record = isRecord(data) ? data : {};
  return textValue(record, ["archive_format", "format", "graph_schema_version", "schema_version"], ".latticebrain");
}

function backupHealthLabel(data: unknown) {
  const record = isRecord(data) ? data : {};
  const count = record.count || record.backups || record.available;
  if (count !== undefined && count !== null && count !== "") return `${count} backups`;
  return "Backups ready";
}

function summarizeCareResult(result: ApiResult) {
  if (!result.ok) return result.error || "Brain care action could not complete.";
  const data = isRecord(result.data) ? result.data : {};
  const directMessage = textValue(data, ["message", "status", "path", "archive_path", "backup_path", "export_path"]);
  if (directMessage) return directMessage;
  return "Brain care action completed.";
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function layoutGraphNodes(nodes: KnowledgeConcept[], radiusX: number, radiusY: number) {
  return nodes.map((node, index) => {
    const point = polarPoint(index, nodes.length, radiusX, radiusY, -88);
    return { node, x: point.x, y: point.y };
  });
}

function polarPoint(index: number, total: number, radiusX: number, radiusY: number, offsetDegrees = -90) {
  const count = Math.max(total, 1);
  const angle = ((360 / count) * index + offsetDegrees) * Math.PI / 180;
  return {
    x: 50 + Math.cos(angle) * radiusX,
    y: 50 + Math.sin(angle) * radiusY,
  };
}

function layerStyle(values: Record<string, string>) {
  return values as React.CSSProperties;
}

function uniqueById<T extends { id: string }>(items: T[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function isRecord(value: unknown): value is ApiRecord {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function textValue(record: ApiRecord, keys: string[], fallback = "") {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return fallback;
}

function titleValue(record: ApiRecord, keys: string[], fallback = "") {
  const value = textValue(record, keys, fallback);
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function numberValue(record: ApiRecord, keys: string[]) {
  for (const key of keys) {
    const value = Number(record[key]);
    if (Number.isFinite(value)) return value;
  }
  return 0;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}
