// First Value Loop ("30초 체험") track state — backlog #3. Same tiny
// localStorage pattern as firstFive.ts: flags only ever move forward so the
// track never re-prompts a user who already went through it. Whether the demo
// corpus is actually installed stays server-truth (GET /api/setup/demo-corpus);
// only the user's progress through the track lives here.
export type FirstValueLoopState = {
  dismissed: boolean;
  // The user sent at least one suggested demo question through chat.
  asked: boolean;
  // The user tried the follow-up "make an HTML page from this" step.
  fileGenTried: boolean;
};

const DISMISSED_KEY = "lattice.firstValueLoop.dismissed";
const ASKED_KEY = "lattice.firstValueLoop.asked";
const FILEGEN_KEY = "lattice.firstValueLoop.fileGenTried";

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

export function readFirstValueLoopState(): FirstValueLoopState {
  return {
    dismissed: readFlag(DISMISSED_KEY),
    asked: readFlag(ASKED_KEY),
    fileGenTried: readFlag(FILEGEN_KEY),
  };
}

export function markFirstValueLoopAsked(): FirstValueLoopState {
  writeFlag(ASKED_KEY);
  return readFirstValueLoopState();
}

export function markFirstValueLoopFileGenTried(): FirstValueLoopState {
  writeFlag(FILEGEN_KEY);
  return readFirstValueLoopState();
}

export function dismissFirstValueLoop(): FirstValueLoopState {
  writeFlag(DISMISSED_KEY);
  return readFirstValueLoopState();
}

export function shouldShowFirstValueLoop(state: FirstValueLoopState) {
  return !state.dismissed;
}
