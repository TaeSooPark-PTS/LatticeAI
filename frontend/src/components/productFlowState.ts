// Tiny, dependency-free helper kept separate from ProductFlow so the app shell
// can read the completion flag synchronously without eagerly pulling the whole
// onboarding chunk (which is only needed for first-run users).
const FLOW_COMPLETE_KEY = "lattice.productFlow.complete";

export function readProductFlowComplete() {
  try {
    return localStorage.getItem(FLOW_COMPLETE_KEY) === "true";
  } catch {}
  return false;
}

export function markProductFlowComplete() {
  try { localStorage.setItem(FLOW_COMPLETE_KEY, "true"); } catch {}
}
