import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ImagePlus, Send, X } from "lucide-react";
import { latticeApi } from "@/api/client";
import { Button } from "@/components/ui/button";
import { LivingBrain, triggerBrainRecall } from "@/components/LivingBrain";
import { ProductFlow, readProductFlowComplete } from "@/components/ProductFlow";
import { useAppStore } from "@/store/appStore";
import { asArray } from "@/lib/utils";









export default function App() {
  const { theme, setTheme } = useAppStore();
  const [flowComplete, setFlowComplete] = React.useState(readProductFlowComplete);
  const { state: brainState, intensity, setBrain } = useBrainState();

  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // ⌘K focuses the home composer
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        const ta = document.querySelector<HTMLTextAreaElement>(".brain-composer textarea");
        ta?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!flowComplete) {
    return <ProductFlow onComplete={() => { setFlowComplete(true); }} />;
  }

  return (
    <div className="brain-space">
      <div className="brain-field" />

      {/* The Brain is the interactive entry point.
          Click it to travel deeper: living presence → memories → concepts/relationships → the emergent full Knowledge Graph.
          The graph grows out of the living mind rather than being a separate destination. */}
      <BrainHome
        brainState={brainState}
        intensity={intensity}
        onBrainChange={setBrain}
      />
    </div>
  );
}

/* --------------------- Supporting hooks & tiny pieces (kept in-file for speed of this release) --------------------- */

function useBrainState() {
  const [state, setState] = React.useState<"idle" | "listening" | "thinking" | "recalling" | "synthesizing" | "resting">("idle");
  const [intensity, setIntensity] = React.useState(0.58);
  const setBrain = React.useCallback((next: any, i?: number) => {
    setState(next);
    if (i !== undefined) setIntensity(Math.max(0.38, Math.min(1, i)));
  }, []);
  return { state, intensity, setBrain };
}

function BrainHome({ brainState, intensity, onBrainChange, onEnterDepth }: any) {
  const qc = useQueryClient();
  const [messages, setMessages] = React.useState<any[]>([]);
  const [draft, setDraft] = React.useState("");
  const [imageData, setImageData] = React.useState<string | null>(null);
  const [streaming, setStreaming] = React.useState(false);
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const streamRef = React.useRef<HTMLDivElement>(null);

  // Progressive exploration depth (0 = pure living Brain + chat; 5 = full knowledge graph emerging from it)
  const [explorationDepth, setExplorationDepth] = React.useState(0);

  // Real data for emergence
  const memoriesQ = useQuery({ queryKey: ["memoryManager"], queryFn: latticeApi.memoryManager });
  const graphQ = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph });

  const memoryItems = React.useMemo(() => {
    const data: any = memoriesQ.data?.data;
    const sources = asArray(data?.sources || data?.tiers || []);
    return sources.slice(0, 7).map((s: any, idx: number) => ({
      id: s.id || `mem-${idx}`,
      title: s.title || s.label || s.source || "Memory",
      type: s.type || s.source_type || "memory"
    }));
  }, [memoriesQ.data]);

  const knowledgeItems = React.useMemo(() => {
    // Use graph nodes as "concepts" for mid layers
    const g: any = graphQ.data?.data;
    const nodes = asArray(g?.nodes || []).slice(0, 8);
    return nodes.map((n: any, idx: number) => ({
      id: n.id || `concept-${idx}`,
      label: n.title || n.label || n.name || "Concept",
      group: n.group || n.type || "idea"
    }));
  }, [graphQ.data]);

  const relationshipLinks = React.useMemo(() => {
    const g: any = graphQ.data?.data;
    const edges = asArray(g?.edges || []).slice(0, 5);
    return edges.map((e: any, idx: number) => ({
      id: `rel-${idx}`,
      source: e.source || e.from,
      target: e.target || e.to,
      label: e.label || e.type || "relates"
    }));
  }, [graphQ.data]);

  const modelsQ = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const modelName = React.useMemo(() => {
    const d: any = modelsQ.data?.data;
    if (!d) return "your mind";
    const loaded = asArray(d.loaded || []);
    return loaded[0]?.name || loaded[0]?.id || "local mind";
  }, [modelsQ.data]);

  React.useEffect(() => {
    if (streaming) onBrainChange("thinking", 0.94);
    else if (draft.trim().length > 4) onBrainChange("listening", 0.76);
    else onBrainChange("idle", 0.58);
  }, [streaming, draft, onBrainChange]);

  async function send() {
    const text = draft.trim();
    if (!text || streaming) return;
    const cid = conversationId || `brain-${Date.now()}`;
    if (!conversationId) setConversationId(cid);

    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setDraft("");
    setImageData(null);
    setStreaming(true);
    onBrainChange("thinking", 0.96);

    try {
      await latticeApi.streamChat(
        { message: text, conversation_id: cid, image_data: imageData || undefined },
        {
          onChunk: (_d, full) => {
            setMessages((prev) => { const n = [...prev]; n[n.length - 1] = { role: "assistant", content: full }; return n; });
          },
          onTrace: (t) => { if (t) { onBrainChange("recalling", 0.9); triggerBrainRecall(); setTimeout(() => onBrainChange("thinking", 0.9), 900); } },
        }
      );
    } finally {
      setStreaming(false);
      qc.invalidateQueries({ queryKey: ["chatHistory"] });
      qc.invalidateQueries({ queryKey: ["memoryManager"] });
      onBrainChange("idle", 0.61);
    }
  }

  React.useEffect(() => { const el = streamRef.current; if (el) el.scrollTop = el.scrollHeight; }, [messages]);

  // Click the living Brain to travel deeper — progressive revelation
  const deepen = () => {
    setExplorationDepth(d => {
      const next = Math.min(5, d + 1);
      if (next >= 2) onBrainChange("recalling", 0.8);
      if (next === 5) onBrainChange("synthesizing", 0.9);
      return next;
    });
  };

  const surface = () => {
    setExplorationDepth(0);
    onBrainChange("idle", 0.6);
  };

  // Derive positioned orbs/nodes for the current depth (emerge around the central Brain)
  const layerElements = React.useMemo(() => {
    const els: React.ReactNode[] = [];
    const baseAngle = 28;

    if (explorationDepth >= 1 && memoryItems.length) {
      memoryItems.forEach((item, i) => {
        const angle = (baseAngle * i) % 360;
        const radius = 138 + (i % 3) * 11;
        const style: React.CSSProperties = {
          left: `calc(50% + ${Math.cos(angle * Math.PI / 180) * radius}px)`,
          top: `calc(50% + ${Math.sin(angle * Math.PI / 180) * (radius * 0.72)}px)`,
          transform: `translate(-50%, -50%) scale(${0.85 + (explorationDepth - 1) * 0.06})`
        };
        els.push(
          <div
            key={item.id}
            className="memory-orb"
            style={style}
            onClick={(e) => {
              e.stopPropagation();
              triggerBrainRecall();
              // Surface the memory gently into awareness
              setMessages(m => [...m, { role: "assistant", content: `I am recalling: ${item.title}` }]);
            }}
            title={`Recall: ${item.title}`}
          >
            {item.title.slice(0, 28)}
          </div>
        );
      });
    }

    if (explorationDepth >= 3 && knowledgeItems.length) {
      knowledgeItems.forEach((item, i) => {
        const angle = (baseAngle * 1.7 * i + 55) % 360;
        const radius = 92 + (i % 2) * 18;
        const style: React.CSSProperties = {
          left: `calc(50% + ${Math.cos(angle * Math.PI / 180) * radius}px)`,
          top: `calc(50% + ${Math.sin(angle * Math.PI / 180) * (radius * 0.68)}px)`,
          transform: `translate(-50%, -50%)`
        };
        els.push(
          <div
            key={`k-${item.id}`}
            className="knowledge-node"
            style={style}
            onClick={(e) => { e.stopPropagation(); triggerBrainRecall(); }}
          >
            {item.label.slice(0, 22)}
          </div>
        );
      });
    }

    if (explorationDepth >= 4 && relationshipLinks.length && knowledgeItems.length) {
      // Simple emerging relationship lines between a few positioned items
      relationshipLinks.forEach((link, i) => {
        // Approximate positions for demo (in real would use layout)
        const sIdx = i % knowledgeItems.length;
        const tIdx = (i + 2) % knowledgeItems.length;
        const angleS = (baseAngle * 1.7 * sIdx + 55) % 360;
        const angleT = (baseAngle * 1.7 * tIdx + 55) % 360;
        const r = 105;
        const x1 = 50 + Math.cos(angleS * Math.PI / 180) * (r / 220) * 100;
        const y1 = 48 + Math.sin(angleS * Math.PI / 180) * (r / 300) * 100;
        const x2 = 50 + Math.cos(angleT * Math.PI / 180) * (r / 220) * 100;
        const y2 = 48 + Math.sin(angleT * Math.PI / 180) * (r / 300) * 100;

        els.push(
          <div
            key={`edge-${i}`}
            className="relationship-edge"
            style={{
              left: `${Math.min(x1, x2)}%`,
              top: `${(y1 + y2) / 2}%`,
              width: `${Math.abs(x2 - x1)}%`,
              transform: `rotate(${Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI}deg)`,
              transformOrigin: "left center"
            }}
          />
        );
      });
    }

    if (explorationDepth >= 5) {
      els.push(
        <div key="core-graph" className="mind-core-graph" onClick={e => e.stopPropagation()}>
          <div style={{ padding: "1rem", fontSize: "0.78rem", color: "hsl(var(--fg-muted))" }}>
            The living core of your mind.<br />
            {knowledgeItems.length} concepts • {relationshipLinks.length} visible threads.<br />
            <span style={{ opacity: 0.6 }}>Full search, traversal, and the complete Lattice live here.</span>
          </div>
          {/* Simple textual emergence of nodes for level 5 — real graph data */}
          <div style={{ padding: "0 1rem 1rem", display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
            {knowledgeItems.slice(0, 6).map((k: any) => (
              <span key={k.id} style={{ fontSize: "0.7rem", background: "hsl(var(--brain-core)/0.15)", padding: "1px 6px", borderRadius: 4 }}>{k.label}</span>
            ))}
          </div>
        </div>
      );
    }

    return els;
  }, [explorationDepth, memoryItems, knowledgeItems, relationshipLinks]);

  const depthLabel = ["Surface", "Echoes", "Concepts", "Threads", "The Lattice", "Core Lattice"][Math.min(explorationDepth, 5)];

  return (
    <>
      <div className="brain-presence">
        <div
          className="brain-exploration"
          data-depth={explorationDepth}
          onClick={() => { if (explorationDepth < 5) deepen(); }}
        >
          <LivingBrain
            state={brainState}
            intensity={intensity + explorationDepth * 0.04}
            size="large"
            depth={explorationDepth}
            onInteract={deepen}
          />

          {/* The mind field — layers emerge from the living Brain when you travel deeper */}
          <div className="brain-field-layer">
            {layerElements}
          </div>

          {explorationDepth > 0 && (
            <button className="brain-surface-control" onClick={(e) => { e.stopPropagation(); surface(); }}>
              ↑ Surface
            </button>
          )}

          <div className="brain-depth-indicator">
            {depthLabel} — click the Brain to go deeper
          </div>
        </div>
      </div>

      <div className="brain-conversation">
        <div className="brain-conversation-header">
          <div style={{ opacity: 0.55 }}>with your mind {explorationDepth > 0 ? `· exploring depth ${explorationDepth}` : ""}</div>
          <div style={{ fontSize: "0.68rem", opacity: 0.45 }}>{modelName}</div>
        </div>

        <div ref={streamRef} className="brain-stream">
          {messages.length === 0 ? (
            <div className="mind-empty">
              <div style={{ fontSize: "13px", letterSpacing: "1.5px", textTransform: "uppercase", opacity: 0.5, marginBottom: "6px" }}>BEGIN</div>
              <div style={{ fontSize: "1.18rem" }}>What are you thinking about?</div>
              <div style={{ marginTop: "0.4rem", fontSize: "0.85rem", opacity: 0.6 }}>Click the living Brain to begin travelling into your knowledge.</div>
            </div>
          ) : messages.map((m, i) => (
            <div key={i} className={`brain-message ${m.role === "user" ? "user" : "assistant"}`}>
              <div className="brain-message-bubble">{m.content}</div>
            </div>
          ))}
        </div>

        <div className="brain-composer">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }}
            placeholder="Talk to your Brain…"
          />
          <div className="brain-composer-actions">
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-border/60 px-3 py-1 text-xs active:bg-white/5">
              <ImagePlus className="h-3.5 w-3.5" /> <span>Image</span>
              <input type="file" accept="image/*" className="sr-only" onChange={async (e) => {
                const f = e.target.files?.[0]; if (f) { const r = new FileReader(); r.onload = () => setImageData(r.result as string); r.readAsDataURL(f); }
              }} />
            </label>
            <Button onClick={() => void send()} disabled={!draft.trim() || streaming} className="rounded-full px-5"><Send className="h-4 w-4" /> Send</Button>
          </div>
        </div>
      </div>

      {/* Optional traditional depths still available but the primary path is through the Brain */}
      <div className="depths">
        <button className="depth" onClick={() => onEnterDepth("memory")}>Memory</button>
        <button className="depth" onClick={() => onEnterDepth("knowledge")}>Knowledge</button>
        <button className="depth" onClick={() => onEnterDepth("relationships")}>Connections</button>
        <button className="depth" onClick={() => onEnterDepth("map")}>The Map</button>
      </div>
    </>
  );
}

// (Old MindChamber removed. All progressive exploration now happens by interacting directly with the living Brain in the main view.)
