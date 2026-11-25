const FLOW_COMPLETE_KEY = "lattice.productFlow.complete";

const LEGACY_COMPLETE_PREFIX = "ltcai_onboarding_complete";

function readLegacyComplete(): boolean {
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key && key.startsWith(LEGACY_COMPLETE_PREFIX) && localStorage.getItem(key) === "true") {
      return true;
    }
  }
  return false;
}

export function readProductFlowComplete() {
  try {
    if (localStorage.getItem(FLOW_COMPLETE_KEY) === "true") return true;
    if (readLegacyComplete()) {
      markProductFlowComplete();
      return true;
    }
  } catch {}
  return false;
}

export function markProductFlowComplete() {
  try { localStorage.setItem(FLOW_COMPLETE_KEY, "true"); } catch {}
}
