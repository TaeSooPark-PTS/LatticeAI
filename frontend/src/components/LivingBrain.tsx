import * as React from "react";
import { cn } from "@/lib/utils";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";

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

const THOUGHT_PARTICLES = Array.from({ length: 12 }, (_, index) => index);
const VITAL_SPARKS = [
  { x: 22, y: 32, delay: -0.4, duration: 5.8 },
  { x: 36, y: 18, delay: -2.1, duration: 6.4 },
  { x: 65, y: 22, delay: -3.7, duration: 5.4 },
  { x: 79, y: 42, delay: -1.3, duration: 6.8 },
  { x: 68, y: 72, delay: -4.5, duration: 5.9 },
  { x: 31, y: 75, delay: -2.8, duration: 6.2 },
] as const;

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
  const language = useAppStore((store) => store.language);
  const organismRef = React.useRef<HTMLButtonElement>(null);
  const pulseTimerRef = React.useRef<number | null>(null);
  const tiltFrameRef = React.useRef<number | null>(null);
  const pendingTiltRef = React.useRef({ x: 0, y: 0 });
  const gradientPrefix = React.useId().replace(/:/g, "");
  const gradients = {
    left: `${gradientPrefix}-lobe-left`,
    right: `${gradientPrefix}-lobe-right`,
    bridge: `${gradientPrefix}-bridge`,
    stem: `${gradientPrefix}-stem`,
    sheen: `${gradientPrefix}-sheen`,
  };

  const firePulse = React.useCallback(() => {
    if (organismRef.current) {
      organismRef.current.classList.add("pulse");
      if (pulseTimerRef.current !== null) window.clearTimeout(pulseTimerRef.current);
      pulseTimerRef.current = window.setTimeout(() => {
        if (organismRef.current) organismRef.current.classList.remove("pulse");
      }, 1350);
    }
    onPulse?.();
  }, [onPulse]);

  // Every visible Brain responds to real recall and ingestion events.
  React.useEffect(() => {
    const handler = () => firePulse();
    window.addEventListener("brain:recall", handler as EventListener);
    return () => {
      window.removeEventListener("brain:recall", handler as EventListener);
      if (pulseTimerRef.current !== null) window.clearTimeout(pulseTimerRef.current);
    };
  }, [firePulse]);

  // Auto gentle pulse when recalling or high intensity
  React.useEffect(() => {
    if (state === "recalling" || intensity > 0.82) {
      const t = window.setTimeout(() => firePulse(), 180);
      return () => clearTimeout(t);
    }
  }, [state, intensity, firePulse]);

  const dataState = state;
  const isLarge = size === "large";
  const isTrace = size === "trace";

  const dynamicIntensity = Math.max(0.35, Math.min(1, intensity));
  const effectiveDepth = Math.max(0, Math.min(5, depth || 0));
  const isBusy = state === "thinking" || state === "recalling" || state === "synthesizing" || state === "acting";
  const handleClick = () => {
    firePulse();
    // Keep the Brain responsive to touch while it is working, but do not
    // navigate away and orphan an active request or ingestion.
    if (isBusy) return;
    onInteract?.();
  };

  // 3D tilt — the orb leans toward the pointer so it reads as a volume, not a sticker.
  const reducedMotionRef = React.useRef(false);
  React.useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => { reducedMotionRef.current = query.matches; };
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  const handleTilt = React.useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (reducedMotionRef.current || event.pointerType === "touch") return;
    const el = organismRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const nx = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    const ny = ((event.clientY - rect.top) / rect.height) * 2 - 1;
    pendingTiltRef.current = { x: ny * -9, y: nx * 11 };
    if (tiltFrameRef.current !== null) return;
    tiltFrameRef.current = window.requestAnimationFrame(() => {
      tiltFrameRef.current = null;
      const target = organismRef.current;
      if (!target) return;
      target.style.setProperty("--tilt-y", `${pendingTiltRef.current.y.toFixed(2)}deg`);
      target.style.setProperty("--tilt-x", `${pendingTiltRef.current.x.toFixed(2)}deg`);
    });
  }, []);

  const resetTilt = React.useCallback(() => {
    if (tiltFrameRef.current !== null) {
      window.cancelAnimationFrame(tiltFrameRef.current);
      tiltFrameRef.current = null;
    }
    const el = organismRef.current;
    if (!el) return;
    el.style.setProperty("--tilt-y", "0deg");
    el.style.setProperty("--tilt-x", "0deg");
  }, []);

  React.useEffect(() => () => {
    if (tiltFrameRef.current !== null) window.cancelAnimationFrame(tiltFrameRef.current);
  }, []);

  return (
    <div
      className={cn(
        "brain-presence select-none",
        isLarge && "large",
        isTrace && "trace",
        className,
        effectiveDepth > 0 && "is-exploring"
      )}
      aria-label={t(language, "brain.living.aria")}
      role="group"
      data-depth={effectiveDepth}
    >
      <button
        type="button"
        ref={organismRef}
        className={cn("brain-organism", `size-${size}`, `depth-${effectiveDepth}`)}
        data-state={dataState}
        data-testid="living-brain"
        aria-label={t(language, effectiveDepth < 5 ? "brain.living.open" : "brain.living.graph")}
        aria-busy={isBusy}
        style={{
          "--brain-scale": `${0.96 + (dynamicIntensity - 0.5) * 0.09 + effectiveDepth * 0.015}`,
        } as React.CSSProperties}
        onClick={handleClick}
        onPointerMove={handleTilt}
        onPointerLeave={resetTilt}
        title={t(language, effectiveDepth < 5 ? "brain.living.open" : "brain.living.graph")}
      >
        <div className="brain-vital-field" aria-hidden="true">
          <span className="brain-vital-ring is-primary" />
          <span className="brain-vital-ring is-echo" />
          <span className="brain-orbit is-near"><i /></span>
          <span className="brain-orbit is-far"><i /></span>
          {VITAL_SPARKS.map((spark, index) => (
            <i
              key={`${spark.x}-${spark.y}`}
              className="brain-vital-spark"
              style={{
                left: `${spark.x}%`,
                top: `${spark.y}%`,
                animationDelay: `${spark.delay}s`,
                animationDuration: `${spark.duration}s`,
                "--spark-index": index,
              } as React.CSSProperties}
            />
          ))}
        </div>

        {/* Living anatomical presence. The glow opens with depth; the folds make it unmistakably a Brain. */}
        <div className="brain-core" style={{ "--core-scale": `${1 + effectiveDepth * 0.045}` } as React.CSSProperties}>
          <div className="brain-body-motion">
            <svg className="brain-anatomy" viewBox="0 0 220 174" aria-hidden>
            <defs>
              {/* 상단 좌측 광원 기준의 볼륨 셰이딩 — 로브가 부풀어 보이게 한다 */}
              <radialGradient id={gradients.left} cx="34%" cy="26%" r="85%">
                <stop offset="0%" stopColor="hsl(var(--brain-halo) / 0.8)" />
                <stop offset="42%" stopColor="hsl(var(--brain-core) / 0.46)" />
                <stop offset="78%" stopColor="hsl(var(--brain-core) / 0.2)" />
                <stop offset="100%" stopColor="hsl(228 30% 8% / 0.28)" />
              </radialGradient>
              <radialGradient id={gradients.right} cx="62%" cy="24%" r="88%">
                <stop offset="0%" stopColor="hsl(var(--connection) / 0.66)" />
                <stop offset="46%" stopColor="hsl(var(--connection) / 0.32)" />
                <stop offset="80%" stopColor="hsl(var(--connection) / 0.14)" />
                <stop offset="100%" stopColor="hsl(228 30% 8% / 0.3)" />
              </radialGradient>
              <radialGradient id={gradients.bridge} cx="50%" cy="30%" r="80%">
                <stop offset="0%" stopColor="hsl(var(--memory) / 0.55)" />
                <stop offset="100%" stopColor="hsl(var(--memory) / 0.1)" />
              </radialGradient>
              <linearGradient id={gradients.stem} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="hsl(var(--knowledge) / 0.42)" />
                <stop offset="100%" stopColor="hsl(var(--knowledge) / 0.08)" />
              </linearGradient>
              <linearGradient id={gradients.sheen} x1="0" y1="0" x2="0.4" y2="1">
                <stop offset="0%" stopColor="hsl(40 60% 98% / 0.5)" />
                <stop offset="70%" stopColor="hsl(40 60% 98% / 0.06)" />
                <stop offset="100%" stopColor="hsl(40 60% 98% / 0)" />
              </linearGradient>
            </defs>
            <path
              className="brain-lobe brain-lobe-left"
              style={{ fill: `url(#${gradients.left})` }}
              d="M102 30c-13-20-44-19-55 1-18 1-29 16-28 33-13 8-18 25-11 39-9 16-1 36 17 42 5 19 27 26 43 15 13 10 33 8 43-5 5-7 8-16 8-27V52c0-8-6-17-17-22Z"
            />
            <path
              className="brain-lobe brain-lobe-right"
              style={{ fill: `url(#${gradients.right})` }}
              d="M118 30c13-20 44-19 55 1 18 1 29 16 28 33 13 8 18 25 11 39 9 16 1 36-17 42-5 19-27 26-43 15-13 10-33 8-43-5-5-7-8-16-8-27V52c0-8 6-17 17-22Z"
            />
            <path className="brain-bridge" style={{ fill: `url(#${gradients.bridge})` }} d="M103 48c9-8 24-8 33 0 7 6 9 16 5 25-5 11-16 15-31 12-15 3-26-1-31-12-4-9-2-19 5-25 5-4 12-6 19 0Z" />
            <path className="brain-stem" style={{ fill: `url(#${gradients.stem})` }} d="M92 137c10 9 26 9 36 0 1 14 7 25 20 33H76c12-8 17-19 16-33Z" />
            <path className="brain-fold fold-a" d="M48 50c18-11 38-8 47 8" />
            <path className="brain-fold fold-b" d="M34 82c22-8 45-5 58 8" />
            <path className="brain-fold fold-c" d="M43 119c18 5 35 2 49-11" />
            <path className="brain-fold fold-d" d="M172 50c-18-11-38-8-47 8" />
            <path className="brain-fold fold-e" d="M186 82c-22-8-45-5-58 8" />
            <path className="brain-fold fold-f" d="M177 119c-18 5-35 2-49-11" />
            <path className="brain-fold fold-mid" d="M110 38c-5 30-5 70 0 112" />
            {/* 광택 — 위쪽에서 떨어지는 반사광 한 줄이 표면의 곡률을 말해준다 */}
            <path
              className="brain-sheen"
              style={{ fill: `url(#${gradients.sheen})` }}
              d="M58 38c22-16 52-20 78-10-30-2-58 6-74 22-8 8-16 6-4-12Z"
            />
            </svg>
          </div>
        </div>

        {/* Breathing field expands as we go deeper. */}
        <div
          className="brain-aura"
          style={{
            animationDuration: state === "thinking" ? "1.65s" : state === "recalling" ? "2.4s" : "6.8s",
            "--aura-scale": `${1 + effectiveDepth * 0.12}`,
            opacity: 0.65 + effectiveDepth * 0.05,
            boxShadow: effectiveDepth > 2 ? "0 0 60px hsl(var(--brain-core) / 0.25)" : "none"
          } as React.CSSProperties}
        />

        {/* Thought activity — increases and starts to "resolve" into structure at higher depths */}
        <div className="thought-activity" aria-hidden>
          {THOUGHT_PARTICLES.slice(0, Math.min(12, 5 + effectiveDepth * 2)).map((i) => (
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

      </button>

      {showLabel && !isTrace && (
        <div className="brain-presence-label" data-state={state}>
          <span className="dot" />
          {label || (effectiveDepth > 0
            ? t(language, "brain.living.depth", { depth: effectiveDepth })
            : t(language, humanStateKey(state)))}
        </div>
      )}
    </div>
  );
}

function humanStateKey(s: BrainState) {
  switch (s) {
    case "listening": return "brain.living.state.listening";
    case "thinking": return "brain.living.state.thinking";
    case "recalling": return "brain.living.state.recalling";
    case "synthesizing": return "brain.living.state.synthesizing";
    case "planning": return "brain.living.state.planning";
    case "acting": return "brain.living.state.acting";
    case "resting": return "brain.living.state.resting";
    default: return "brain.living.state.idle";
  }
}

// Helper to broadcast recall pulses from anywhere (conversation, memory surface, etc)
export function triggerBrainRecall() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("brain:recall"));
  }
}
