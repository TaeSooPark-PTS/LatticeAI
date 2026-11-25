import { workspaceLibraryCopy } from "./workspace/library";
import { workspaceSystemCopy } from "./workspace/system";
import { workspaceActCopy } from "./workspace/act";
import { workspaceAdminCopy } from "./workspace/admin";
import { workspaceCaptureCopy } from "./workspace/capture";
import { workspaceReviewCopy } from "./workspace/review";
import { registerCopy } from "./registry";
import type { NamespaceCopy } from "./types";

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

registerCopy(workspaceCopy);
