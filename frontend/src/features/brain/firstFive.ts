// First-run "first five minutes" checklist state. A tiny localStorage-backed
// module (same pattern as productFlowState) so the guided card can persist
// progress across sessions without new dependencies. Keys live under
// "lattice.firstFive.*" and only ever move forward: once a step is done or the
// card is dismissed, it never comes back.
export type FirstFiveStep = "ask" | "add" | "learned";

export const FIRST_FIVE_STEPS: readonly FirstFiveStep[] = ["ask", "add", "learned"];

const DONE_KEY_PREFIX = "lattice.firstFive.done.";
const DISMISSED_KEY = "lattice.firstFive.dismissed";

export type FirstFiveState = {
  dismissed: boolean;
  done: Record<FirstFiveStep, boolean>;
};

function readFlag(key: string) {
  try {
    return localStorage.getItem(key) === "true";
  } catch {}
  return false;
}

function writeFlag(key: string) {
  try {
    localStorage.setItem(key, "true");
  } catch {}
}

export function readFirstFiveState(): FirstFiveState {
  const done = {} as Record<FirstFiveStep, boolean>;
  for (const step of FIRST_FIVE_STEPS) done[step] = readFlag(DONE_KEY_PREFIX + step);
  return { dismissed: readFlag(DISMISSED_KEY), done };
}

export function markFirstFiveStepDone(step: FirstFiveStep): FirstFiveState {
  writeFlag(DONE_KEY_PREFIX + step);
  return readFirstFiveState();
}

export function dismissFirstFive(): FirstFiveState {
  writeFlag(DISMISSED_KEY);
  return readFirstFiveState();
}

export function countFirstFiveDone(state: FirstFiveState) {
  return FIRST_FIVE_STEPS.filter((step) => state.done[step]).length;
}

export function isFirstFiveComplete(state: FirstFiveState) {
  return countFirstFiveDone(state) === FIRST_FIVE_STEPS.length;
}

export function shouldShowFirstFive(state: FirstFiveState) {
  return !state.dismissed && !isFirstFiveComplete(state);
}
