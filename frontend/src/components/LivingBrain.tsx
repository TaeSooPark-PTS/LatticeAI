import * as React from "react";
import { cn } from "@/lib/utils";

export type BrainState = "idle" | "listening" | "thinking" | "recalling" | "synthesizing" | "planning" | "acting" | "resting";

export interface LivingBrainProps {
  state?: BrainState;
  intensity?: number; // 0-1 how "alive" it feels right now
  onPulse?: () => void; // allow parent to trigger a memory pulse
  size?: "normal" | "large" | "trace";
  label?: string;
  className?: string;
  showLabel?: boolean;
  depth?: number; // 0-5 progressive exploration depth; higher = more "open" / revealing
  onInteract?: () => void; // called on click to advance exploration (travel deeper)
}

/**
 * The Living Brain — the primary visual and emotional object in the product.
 * It is not decoration. It is the other participant.
 * It breathes. It reacts. It remembers.
 */
export function LivingBrain({
  state = "idle",
  intensity = 0.6,
  onPulse,
  size = "large",
  label,
  className,
  showLabel = true,
  depth = 0,
  onInteract,
}: LivingBrainProps) {
  const [isPulsing, setIsPulsing] = React.useState(false);
  const organismRef = React.useRef<HTMLButtonElement>(null);

  // External trigger for memory / important recall moments
  React.useEffect(() => {
    if (onPulse) {
      const handler = () => firePulse();
      // allow global window event too for simplicity across components
      window.addEventListener("brain:recall", handler as EventListener);
      return () => window.removeEventListener("brain:recall", handler as EventListener);
    }
  }, [onPulse]);

  function firePulse() {
    setIsPulsing(true);
    if (organismRef.current) {
      organismRef.current.classList.add("pulse");
      // clean after animation
      window.setTimeout(() => {
        if (organismRef.current) organismRef.current.classList.remove("pulse");
        setIsPulsing(false);
      }, 1350);
    }
    onPulse?.();
  }

  // Auto gentle pulse when recalling or high intensity
  React.useEffect(() => {
    if ((state === "recalling" || intensity > 0.82) && !isPulsing) {
      const t = window.setTimeout(() => firePulse(), 180);
      return () => clearTimeout(t);
    }
  }, [state, intensity]);

  const dataState = state;
  const isLarge = size === "large";
  const isTrace = size === "trace";

  const dynamicIntensity = Math.max(0.35, Math.min(1, intensity));
  const effectiveDepth = Math.max(0, Math.min(5, depth || 0));
  const canTravel = state !== "thinking";

  const handleClick = () => {
    if (!canTravel) return;
    firePulse();
    onInteract?.();
  };

  return (
    <div
      className={cn(
        "brain-presence select-none",
        isLarge && "large",
        isTrace && "trace",
        className,
        effectiveDepth > 0 && "is-exploring"
      )}
      aria-label="Your living Brain"
      role="group"
      data-depth={effectiveDepth}
    >
      <button
        type="button"
        ref={organismRef}
        className={cn("brain-organism", `size-${size}`, `depth-${effectiveDepth}`)}
        data-state={dataState}
        aria-label={effectiveDepth < 5 ? "Travel deeper into your Brain" : "Rest inside the Knowledge Graph"}
        aria-disabled={!canTravel}
        style={{
          transform: `scale(${0.96 + (dynamicIntensity - 0.5) * 0.09 + effectiveDepth * 0.015})`,
        }}
        onClick={handleClick}
        title={effectiveDepth < 5 ? "Travel deeper into your Brain" : "The core of your knowledge"}
      >
        {/* Living anatomical presence. The glow opens with depth; the folds make it unmistakably a Brain. */}
        <div className="brain-core" style={{ transform: `scale(${1 + effectiveDepth * 0.045})` }}>
          <svg className="brain-anatomy" viewBox="0 0 220 174" aria-hidden>
            <path
              className="brain-lobe brain-lobe-left"
              d="M102 30c-13-20-44-19-55 1-18 1-29 16-28 33-13 8-18 25-11 39-9 16-1 36 17 42 5 19 27 26 43 15 13 10 33 8 43-5 5-7 8-16 8-27V52c0-8-6-17-17-22Z"
            />
            <path
              className="brain-lobe brain-lobe-right"
              d="M118 30c13-20 44-19 55 1 18 1 29 16 28 33 13 8 18 25 11 39 9 16 1 36-17 42-5 19-27 26-43 15-13 10-33 8-43-5-5-7-8-16-8-27V52c0-8 6-17 17-22Z"
            />
            <path className="brain-bridge" d="M103 48c9-8 24-8 33 0 7 6 9 16 5 25-5 11-16 15-31 12-15 3-26-1-31-12-4-9-2-19 5-25 5-4 12-6 19 0Z" />
            <path className="brain-stem" d="M92 137c10 9 26 9 36 0 1 14 7 25 20 33H76c12-8 17-19 16-33Z" />
            <path className="brain-fold fold-a" d="M48 50c18-11 38-8 47 8" />
            <path className="brain-fold fold-b" d="M34 82c22-8 45-5 58 8" />
            <path className="brain-fold fold-c" d="M43 119c18 5 35 2 49-11" />
            <path className="brain-fold fold-d" d="M172 50c-18-11-38-8-47 8" />
            <path className="brain-fold fold-e" d="M186 82c-22-8-45-5-58 8" />
            <path className="brain-fold fold-f" d="M177 119c-18 5-35 2-49-11" />
            <path className="brain-fold fold-mid" d="M110 38c-5 30-5 70 0 112" />
          </svg>
        </div>

        {/* Breathing field expands as we go deeper. */}
        <div
          className="brain-aura"
          style={{
            animationDuration: state === "thinking" ? "1.65s" : state === "recalling" ? "2.4s" : "6.8s",
            transform: `scale(${1 + effectiveDepth * 0.12})`,
            opacity: 0.65 + effectiveDepth * 0.05,
            boxShadow: effectiveDepth > 2 ? "0 0 60px hsl(var(--brain-core) / 0.25)" : "none"
          }}
        />

        {/* Thought activity — increases and starts to "resolve" into structure at higher depths */}
        <div className="thought-activity" aria-hidden>
          {Array.from({ length: Math.min(12, 5 + effectiveDepth * 2) }).map((_, i) => (
            <div
              key={i}
              className={cn("thought-particle", effectiveDepth >= 3 && "resolving")}
              style={{
                left: `${18 + ((i * 13 + effectiveDepth * 4) % 64)}%`,
                top: `${22 + (i % 5) * 14}%`,
                animationDelay: `-${i * 0.55 + (intensity * 1.1) - effectiveDepth * 0.2}s`,
                animationDuration: `${2.8 + (1 - dynamicIntensity) * 1.6 - effectiveDepth * 0.15}s`,
              }}
            />
          ))}
        </div>

        {/* Memory ripples — more and stronger as depth increases (echoes surfacing) */}
        {Array.from({ length: 1 + Math.floor(effectiveDepth / 1.5) }).map((_, i) => (
          <div
            key={i}
            className="memory-ripple"
            aria-hidden
            style={{
              inset: `${18 + i * 6}%`,
              animationDelay: `${180 + i * 220}ms`,
              opacity: 0.55 + effectiveDepth * 0.06
            }}
          />
        ))}

        {/* Relationship structure appears only near the deepest layers. */}
        {effectiveDepth >= 4 && (
          <svg className="brain-inner-structure" viewBox="0 0 100 100" aria-hidden>
            <g stroke="hsl(var(--brain-core) / 0.35)" strokeWidth="0.6" fill="none">
              <circle cx="50" cy="50" r="18" />
              <circle cx="50" cy="50" r="28" />
              <path d="M32 50 Q50 32 68 50" />
              <path d="M32 50 Q50 68 68 50" />
              {/* lattice connections for deeper feel */}
              <circle cx="25" cy="35" r="2" fill="hsl(var(--brain-core) / 0.4)" />
              <circle cx="75" cy="35" r="2" fill="hsl(var(--brain-core) / 0.4)" />
              <circle cx="25" cy="65" r="2" fill="hsl(var(--brain-core) / 0.4)" />
              <circle cx="75" cy="65" r="2" fill="hsl(var(--brain-core) / 0.4)" />
              <path d="M25 35 L50 50 L75 35" stroke="hsl(var(--knowledge)/0.3)" strokeWidth="0.4" />
              <path d="M25 65 L50 50 L75 65" stroke="hsl(var(--knowledge)/0.3)" strokeWidth="0.4" />
            </g>
          </svg>
        )}
      </button>

      {showLabel && !isTrace && (
        <div className="brain-presence-label" data-state={state}>
          <span className="dot" />
          {label || (effectiveDepth > 0 ? `Depth ${effectiveDepth}` : humanState(state))}
        </div>
      )}
    </div>
  );
}

function humanState(s: BrainState) {
  switch (s) {
    case "listening": return "Listening";
    case "thinking": return "Thinking with you";
    case "recalling": return "Remembering";
    case "synthesizing": return "Making sense";
    case "planning": return "Planning";
    case "acting": return "Acting";
    case "resting": return "With you";
    default: return "Here";
  }
}

// Helper to broadcast recall pulses from anywhere (conversation, memory surface, etc)
export function triggerBrainRecall() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("brain:recall"));
  }
}
