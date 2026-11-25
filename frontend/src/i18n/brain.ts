import { brainHomeCopy } from "./brain/home";
import { brainBriefingCopy } from "./brain/briefing";
import { brainChatCopy } from "./brain/chat";
import { brainIngestCopy } from "./brain/ingest";
import { brainMemoryCopy } from "./brain/memory";
import { brainGraphCopy } from "./brain/graph";
import { brainEvidenceCopy } from "./brain/evidence";
import { brainCareCopy } from "./brain/care";
import { brainGovernanceCopy } from "./brain/governance";
import { brainSelfModelCopy } from "./brain/selfModel";
import { registerCopy } from "./registry";
import type { NamespaceCopy } from "./types";

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
    ...brainGovernanceCopy.ko,
    ...brainSelfModelCopy.ko,
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
    ...brainGovernanceCopy.en,
    ...brainSelfModelCopy.en,
  },
};

registerCopy(brainCopy);
