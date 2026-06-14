import * as React from "react";
import { cn } from "@/lib/utils";

export type BrainState = "idle" | "listening" | "thinking" | "recalling" | "synthesizing" | "resting";

export interface LivingBrainProps {
  state?: BrainState;
  intensity?: number; // 0-1 how "alive" it feels right now
  onPulse?: () => void; // allow parent to trigger a memory pulse
  size?: "normal" | "large" | "trace";
  label?: string;
  className?: string;
  showLabel?: boolean;
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
}: LivingBrainProps) {
  const [isPulsing, setIsPulsing] = React.useState(false);
  const organismRef = React.useRef<HTMLDivElement>(null);

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

  return (
    <div
      className={cn(
        "brain-presence select-none",
        isLarge && "large",
        isTrace && "trace",
        className
      )}
      aria-label="Your living Brain"
      role="img"
    >
      <div
        ref={organismRef}
        className={cn("brain-organism", `size-${size}`)}
        data-state={dataState}
        style={{
          transform: `scale(${0.96 + (dynamicIntensity - 0.5) * 0.09})`,
        }}
        onClick={() => {
          if (state !== "thinking") firePulse();
        }}
        title="Your Brain — tap to feel it respond"
      >
        {/* Core luminous presence */}
        <div className="brain-core" />

        {/* Breathing halo */}
        <div
          className="brain-halo"
          style={{ animationDuration: state === "thinking" ? "1.65s" : state === "recalling" ? "2.4s" : "6.8s" }}
        />

        {/* Subtle drifting thought activity */}
        <div className="thought-activity" aria-hidden>
          {Array.from({ length: isTrace ? 2 : 5 }).map((_, i) => (
            <div
              key={i}
              className="thought-particle"
              style={{
                left: `${22 + ((i * 17) % 58)}%`,
                top: `${28 + (i % 3) * 18}%`,
                animationDelay: `-${i * 0.7 + (intensity * 1.1)}s`,
                animationDuration: `${3.4 + (1 - dynamicIntensity) * 1.8}s`,
              }}
            />
          ))}
        </div>

        {/* Memory ripples (triggered) */}
        <div className="memory-ripple" aria-hidden />
        <div className="memory-ripple" style={{ inset: "28%", animationDelay: "180ms" }} aria-hidden />
      </div>

      {showLabel && !isTrace && (
        <div className="brain-presence-label" data-state={state}>
          <span className="dot" />
          {label || humanState(state)}
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
