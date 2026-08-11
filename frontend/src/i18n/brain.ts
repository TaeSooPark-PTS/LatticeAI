import { brainHomeCopy } from "./brain/home";
import { brainBriefingCopy } from "./brain/briefing";
import { brainChatCopy } from "./brain/chat";
import { brainIngestCopy } from "./brain/ingest";
import { brainMemoryCopy } from "./brain/memory";
import { brainGraphCopy } from "./brain/graph";
import { brainEvidenceCopy } from "./brain/evidence";
import { brainCareCopy } from "./brain/care";
import { registerCopy } from "./registry";
import type { NamespaceCopy } from "./types";

// The dictionary lives in ./brain/*, one file per domain. This module is the
// namespace: it merges those parts and registers them exactly once, so every
// importer keeps using "@/i18n/brain" and nothing else moves.
export const brainCopy: NamespaceCopy = {
  ko: {
    ...brainHomeCopy.ko,
    ...brainBriefingCopy.ko,
    ...brainChatCopy.ko,
    ...brainIngestCopy.ko,
    ...brainMemoryCopy.ko,
    ...brainGraphCopy.ko,
    ...brainEvidenceCopy.ko,
    ...brainCareCopy.ko,
  },
  en: {
    ...brainHomeCopy.en,
    ...brainBriefingCopy.en,
    ...brainChatCopy.en,
    ...brainIngestCopy.en,
    ...brainMemoryCopy.en,
    ...brainGraphCopy.en,
    ...brainEvidenceCopy.en,
    ...brainCareCopy.en,
  },
};

// Route-scoped: registered when a Brain surface's lazy chunk loads.
registerCopy(brainCopy);
