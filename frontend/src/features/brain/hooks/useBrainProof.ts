import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { buildBrainBrief, buildBrainProof } from "../brainData";
import { useConversationSession } from "../conversationSession";
import type { BrainProof, Message, MessageProof } from "../types";

export function useBrainProof({
  language,
  messages,
  modelName,
  setMemoryFeedback,
}: {
  language: Language;
  messages: Message[];
  modelName: string;
  setMemoryFeedback: React.Dispatch<React.SetStateAction<string | null>>;
}) {
  const setMessages = useConversationSession((state) => state.setMessages);
  const [lastRecallQuery, setLastRecallQuery] = React.useState("");
  const [detailsRequested, setDetailsRequested] = React.useState(false);
  const brainProofQ = useQuery({
    queryKey: ["memoryBrainProof", lastRecallQuery],
    queryFn: () => latticeApi.memoryBrainProof(lastRecallQuery, 3),
    enabled: detailsRequested || Boolean(lastRecallQuery) || messages.length > 0,
  });
  const brainBriefQ = useQuery({
    queryKey: ["memoryBrainBrief", lastRecallQuery],
    queryFn: () => latticeApi.memoryBrainBrief(lastRecallQuery, 3),
  });
  const brainProof = React.useMemo(
    () => buildBrainProof(brainProofQ.data, modelName),
    [brainProofQ.data, modelName],
  );
  const brainBrief = React.useMemo(
    () => buildBrainBrief(brainBriefQ.data?.data),
    [brainBriefQ.data],
  );

  const attachAnswerProof = React.useCallback(async (query: string): Promise<boolean> => {
    const proofResult = await latticeApi.memoryBrainProof(query, 4);
    if (!proofResult.ok) {
      setMemoryFeedback(t(language, "brain.proof.unavailable", {
        reason: proofResult.error || t(language, "ui.status.unavailable"),
      }));
      return false;
    }
    const proof = buildBrainProof(proofResult, modelName);
    const messageProof = toMessageProof(proof, query, modelName);
    setMessages((items) => {
      const next = [...items];
      for (let index = next.length - 1; index >= 0; index -= 1) {
        if (next[index].role === "assistant") {
          next[index] = { ...next[index], proof: messageProof };
          break;
        }
      }
      return next;
    });
    return true;
  }, [language, modelName, setMemoryFeedback, setMessages]);

  const verifyModelContinuity = React.useCallback(async () => {
    const lastUserMessage = [...messages].reverse().find((message) => message.role === "user");
    const query = lastRecallQuery || lastUserMessage?.content || "";
    if (!query.trim()) {
      setMemoryFeedback(t(language, "brain.modelDemo.needQuestion"));
      return;
    }
    setMemoryFeedback(t(language, "brain.modelDemo.checking", { model: modelName }));
    const attached = await attachAnswerProof(query);
    if (!attached) return;
    setLastRecallQuery(query);
    setMemoryFeedback(t(language, "brain.modelDemo.done", { model: modelName }));
  }, [attachAnswerProof, language, lastRecallQuery, messages, modelName, setMemoryFeedback]);

  return {
    brainProof,
    brainBrief,
    lastRecallQuery,
    setLastRecallQuery,
    requestDetails: () => setDetailsRequested(true),
    attachAnswerProof,
    verifyModelContinuity,
  };
}

function toMessageProof(proof: BrainProof, query: string, fallbackModelName: string): MessageProof {
  return {
    query,
    model: proof.modelContinuity.activeModel || fallbackModelName,
    provenAcrossModels: proof.modelContinuity.proven && proof.claims.keepsContextAcrossModels,
    citations: proof.recall.items.slice(0, 4).map((item) => ({
      id: item.id,
      source: item.source,
      title: item.title,
      snippet: item.snippet,
      matchedTerms: item.matchedTerms,
      confidence: item.confidence,
      score: item.score,
      locator: item.locator,
    })),
  };
}
