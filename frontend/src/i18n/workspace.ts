import { workspaceLibraryCopy } from "./workspace/library";
import { workspaceSystemCopy } from "./workspace/system";
import { workspaceActCopy } from "./workspace/act";
import { workspaceAdminCopy } from "./workspace/admin";
import { workspaceCaptureCopy } from "./workspace/capture";
import { workspaceReviewCopy } from "./workspace/review";
import { registerCopy } from "./registry";
import type { NamespaceCopy } from "./types";

// The dictionary lives in ./workspace/*, one file per domain. This module is the
// namespace: it merges those parts and registers them exactly once, so every
// importer keeps using "@/i18n/workspace" and nothing else moves.
export const workspaceCopy: NamespaceCopy = {
  ko: {
    ...workspaceLibraryCopy.ko,
    ...workspaceSystemCopy.ko,
    ...workspaceActCopy.ko,
    ...workspaceAdminCopy.ko,
    ...workspaceCaptureCopy.ko,
    ...workspaceReviewCopy.ko,
  },
  en: {
    ...workspaceLibraryCopy.en,
    ...workspaceSystemCopy.en,
    ...workspaceActCopy.en,
    ...workspaceAdminCopy.en,
    ...workspaceCaptureCopy.en,
    ...workspaceReviewCopy.en,
  },
};

// Route-scoped: registered when Act/Capture/Library/System/Admin loads.
registerCopy(workspaceCopy);
