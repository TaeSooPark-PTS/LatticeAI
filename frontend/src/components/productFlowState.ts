// Tiny, dependency-free helper kept separate from ProductFlow so the app shell
// can read the completion flag synchronously without eagerly pulling the whole
// onboarding chunk (which is only needed for first-run users).
const FLOW_COMPLETE_KEY = "lattice.productFlow.complete";

// Onboarding completion used to be stored under `ltcai_onboarding_complete`
// (and a `_<email>` suffixed variant once workspaces landed). Nothing ever
// migrated those to `lattice.productFlow.complete`, so a user who finished
// onboarding on an older build reads as brand new and is sent back to the
// first-run wake screen on every visit — with their session, workspace, and
// conversations all still present. Honour the legacy flags on read.
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
      // Migrate forward so the scan only happens once per browser.
      markProductFlowComplete();
      return true;
    }
  } catch {}
  return false;
}

export function markProductFlowComplete() {
  try { localStorage.setItem(FLOW_COMPLETE_KEY, "true"); } catch {}
}
