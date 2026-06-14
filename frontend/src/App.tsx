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
  const [depth, setDepth] = React.useState<"home" | "memory" | "knowledge" | "relationships" | "map">("home");

  const { state: brainState, intensity, setBrain } = useBrainState();

  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // ⌘K focuses the home composer; Esc leaves chambers
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        const ta = document.querySelector<HTMLTextAreaElement>(".brain-composer textarea");
        ta?.focus();
      }
      if (e.key === "Escape" && depth !== "home") setDepth("home");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [depth]);

  if (!flowComplete) {
    return <ProductFlow onComplete={() => { setFlowComplete(true); setDepth("home"); }} />;
  }

  const goDepth = (d: any) => {
    setDepth(d);
    if (d !== "home") setBrain("recalling", 0.74);
    else setBrain("idle", 0.6);
  };

  return (
    <div className="brain-space" data-depth={depth}>
      <div className="brain-field" />

      {depth === "home" ? (
        <BrainHome
          brainState={brainState}
          intensity={intensity}
          onBrainChange={setBrain}
          onEnterDepth={goDepth}
        />
      ) : (
        <MindChamber depth={depth} onExit={() => setDepth("home")} brainState={brainState} />
      )}
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

  return (
    <>
      <div className="brain-presence">
        <LivingBrain state={brainState} intensity={intensity} size="large" />
      </div>

      <div className="brain-conversation">
        <div className="brain-conversation-header">
          <div style={{ opacity: 0.55 }}>with your mind</div>
          <div style={{ fontSize: "0.68rem", opacity: 0.45 }}>{modelName}</div>
        </div>

        <div ref={streamRef} className="brain-stream">
          {messages.length === 0 ? (
            <div className="mind-empty">
              <div style={{ fontSize: "13px", letterSpacing: "1.5px", textTransform: "uppercase", opacity: 0.5, marginBottom: "6px" }}>BEGIN</div>
              <div style={{ fontSize: "1.18rem" }}>What are you thinking about?</div>
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

      <div className="depths">
        <button className="depth" onClick={() => onEnterDepth("memory")}>Memory</button>
        <button className="depth" onClick={() => onEnterDepth("knowledge")}>Knowledge</button>
        <button className="depth" onClick={() => onEnterDepth("relationships")}>Connections</button>
        <button className="depth" onClick={() => onEnterDepth("map")}>The Map</button>
      </div>
    </>
  );
}

function MindChamber({ depth, onExit, brainState }: any) {
  const title = depth === "memory" ? "Memories" : depth === "knowledge" ? "What your Brain knows" : depth === "relationships" ? "How everything connects" : "The Map";
  const sub = depth === "memory" ? "The life you have lived with your mind." : depth === "knowledge" ? "What has settled into understanding." : depth === "relationships" ? "The quiet threads between the things that matter." : "The architecture of what you know. Walk slowly.";

  return (
    <div className="mind-chamber">
      <div className="chamber-head">
        <button onClick={onExit} className="flex items-center gap-2 text-sm opacity-70 hover:opacity-100"><ArrowLeft className="h-4 w-4" /> Back to the Brain</button>
        <div className="flex items-center gap-3"><div className="brain-trace" /> <div className="chamber-title">{title}</div></div>
        <button onClick={onExit}><X className="h-5 w-5 opacity-60" /></button>
      </div>
      <div className="chamber-body">
        <div style={{ fontSize: "1.32rem", fontWeight: 620, marginBottom: "1.4rem" }}>{title}<div style={{ fontSize: "0.95rem", color: "hsl(var(--fg-muted))", marginTop: "4px" }}>{sub}</div></div>

        {depth === "map" && <div className="mind-map-frame"><div style={{ padding: "2.25rem", opacity: 0.75, fontSize: "13px" }}>The living structure of your knowledge. Every node is something you gave it. Every line is a connection it discovered or you lived.<br /><br />This is the deepest, most contemplative layer. It is not a tool for work. It is a place to understand the shape of your own mind.</div></div>}
        {depth === "memory" && <div className="text-[15px] leading-relaxed opacity-85">Everything you have told it, every document you fed it, every decision made together. Over months and years this becomes a second, truer autobiography.</div>}
        {depth === "knowledge" && <div className="text-[15px] leading-relaxed opacity-85">Not raw files. What the Brain has come to believe, prefer, and hold as true because of the life you have shared with it.</div>}
        {depth === "relationships" && <div className="text-[15px] leading-relaxed opacity-85">Some ideas are close because they were born together. Others grew toward each other slowly. This is the living fabric of your thinking.</div>}
      </div>
      <div style={{ position: "fixed", bottom: 22, right: 22, zIndex: 70, opacity: 0.55 }}>
        <LivingBrain state={brainState} size="trace" showLabel={false} />
      </div>
    </div>
  );
}
