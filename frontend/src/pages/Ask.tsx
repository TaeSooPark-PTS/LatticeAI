import * as React from "react";
import { type BrainState } from "@/components/LivingBrain";
import { BrainHome } from "@/features/brain/BrainHome";

export function AskPage() {
  const [brainPresence, setBrainPresence] = React.useState<{ state: BrainState; intensity: number }>({
    state: "idle",
    intensity: 0.58,
  });
  const setBrain = React.useCallback((state: BrainState, intensity = 0.58) => {
    setBrainPresence({ state, intensity });
  }, []);
  return <BrainHome brainState={brainPresence.state} intensity={brainPresence.intensity} onBrainChange={setBrain} />;
}
