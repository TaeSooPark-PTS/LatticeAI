import * as React from "react";
import { Activity, Brain, CheckCircle2, CircleDotDashed, Loader2, Sparkles, Waves } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn, fmtNumber } from "@/lib/utils";

export type BrainActivity = "idle" | "listening" | "recalling" | "thinking" | "planning" | "acting";

export type BrainVitals = {
  connected?: boolean;
  memories?: number | string | null;
  knowledge?: number | string | null;
  conversations?: number | string | null;
  model?: string | null;
  activityLabel?: string | null;
};

const activityLabels: Record<BrainActivity, string> = {
  idle: "Present",
  listening: "Listening",
  recalling: "Recalling",
  thinking: "Thinking",
  planning: "Planning",
  acting: "Acting",
};

function readable(value: BrainVitals[keyof BrainVitals]) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return fmtNumber(value);
  return String(value);
}

export function LivingBrain({
  activity = "idle",
  vitals,
  compact = false,
  showVitals = false,
  className,
}: {
  activity?: BrainActivity;
  vitals?: BrainVitals;
  compact?: boolean;
  showVitals?: boolean;
  className?: string;
}) {
  const state = vitals?.activityLabel || activityLabels[activity];
  const connected = vitals?.connected !== false;

  return (
    <section
      className={cn("living-brain", compact && "living-brain-compact", className)}
      data-activity={activity}
      aria-label="Living Brain"
    >
      <div className="brain-presence-head">
        <div>
          <div className="brain-presence-kicker"><Brain className="h-4 w-4" /> Lattice Brain</div>
          <h2>The Brain is awake.</h2>
        </div>
        <Badge variant={connected ? "success" : "warning"}>{connected ? "online" : "starting"}</Badge>
      </div>

      <div className="brain-stage" aria-hidden="true">
        <span className="brain-halo brain-halo-a" />
        <span className="brain-halo brain-halo-b" />
        <span className="brain-wave brain-wave-a" />
        <span className="brain-wave brain-wave-b" />
        <span className="brain-wave brain-wave-c" />
        <svg className="brain-organ" viewBox="0 0 440 360" role="img" aria-label="Animated Brain presence">
          <defs>
            <filter id="brainGlow" x="-40%" y="-40%" width="180%" height="180%">
              <feGaussianBlur stdDeviation="10" result="blur" />
              <feColorMatrix in="blur" type="matrix" values="0 0 0 0 0.12 0 0 0 0 0.78 0 0 0 0 0.68 0 0 0 0.52 0" />
              <feBlend in="SourceGraphic" />
            </filter>
          </defs>
          <path className="brain-mass brain-mass-left" d="M214 74c-25-34-82-31-103 7-28 1-50 22-51 51-28 16-39 51-25 81-14 32 6 72 42 80 18 35 72 43 101 14 30 17 71 6 87-25 27-11 43-40 36-69 19-25 14-64-11-84 2-31-31-61-76-55Z" />
          <path className="brain-mass brain-mass-right" d="M224 74c24-35 83-33 105 5 29 0 52 22 53 52 28 16 39 52 24 82 14 33-8 74-45 80-19 34-73 41-101 11-31 16-72 3-86-29-27-12-41-42-33-71-18-27-10-66 18-85-1-30 31-57 65-45Z" />
          <path className="thought-path thought-path-a" d="M106 143c35-30 83-27 113 9 26 31 74 31 110 0" />
          <path className="thought-path thought-path-b" d="M93 218c38 25 75 23 112-8 34-29 75-31 121-6" />
          <path className="thought-path thought-path-c" d="M144 100c9 38 33 59 72 63 42 5 69 27 83 67" />
          <path className="thought-path thought-path-d" d="M149 286c16-44 42-68 78-71 42-4 72-26 89-66" />
          <circle className="memory-pulse pulse-a" cx="125" cy="150" r="7" />
          <circle className="memory-pulse pulse-b" cx="223" cy="164" r="8" />
          <circle className="memory-pulse pulse-c" cx="312" cy="210" r="7" />
          <circle className="memory-pulse pulse-d" cx="183" cy="257" r="6" />
        </svg>
        <div className="brain-state-pill">
          {activity === "thinking" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CircleDotDashed className="h-4 w-4" />}
          <span>{state}</span>
        </div>
      </div>

      {showVitals ? (
        <div className="brain-vitals">
          <div className="brain-vital">
            <Sparkles className="h-4 w-4" />
            <span>Memories</span>
            <strong>{readable(vitals?.memories)}</strong>
          </div>
          <div className="brain-vital">
            <Waves className="h-4 w-4" />
            <span>Knowledge</span>
            <strong>{readable(vitals?.knowledge)}</strong>
          </div>
          <div className="brain-vital">
            <Activity className="h-4 w-4" />
            <span>Activity</span>
            <strong>{readable(vitals?.conversations)}</strong>
          </div>
        </div>
      ) : null}

      {compact ? null : (
        <div className="brain-presence-foot">
          <CheckCircle2 className="h-4 w-4 text-primary" />
          <span>{vitals?.model ? readable(vitals.model) : "Waiting for a local model"}</span>
        </div>
      )}
    </section>
  );
}
