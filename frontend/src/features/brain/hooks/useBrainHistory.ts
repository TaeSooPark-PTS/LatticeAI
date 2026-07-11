import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { buildConversationSummaries, parseConversationMessages } from "../brainData";
import { useConversationSession } from "../conversationSession";

export function useBrainHistory({
  language,
  setMemoryFeedback,
}: {
  language: Language;
  setMemoryFeedback: React.Dispatch<React.SetStateAction<string | null>>;
}) {
  const qc = useQueryClient();
  const conversationId = useConversationSession((state) => state.conversationId);
  const setConversationId = useConversationSession((state) => state.setConversationId);
  const setMessages = useConversationSession((state) => state.setMessages);
  const resetConversation = useConversationSession((state) => state.resetConversation);
  const [historyBusyId, setHistoryBusyId] = React.useState<string | null>(null);
  const historyQ = useQuery({ queryKey: ["chatHistory"], queryFn: latticeApi.chatHistory });
  const pastConversations = React.useMemo(
    () => buildConversationSummaries(historyQ.data?.data),
    [historyQ.data],
  );

  const resumeConversation = React.useCallback(async (id: string, streaming: boolean) => {
    if (historyBusyId || streaming) return;
    setHistoryBusyId(id);
    setMemoryFeedback(t(language, "brain.history.loading"));
    try {
      const result = await latticeApi.conversation(id);
      if (!result.ok) {
        setMemoryFeedback(t(language, "brain.history.loadFailed", { reason: result.error || "unavailable" }));
        return;
      }
      const restored = parseConversationMessages(result.data);
      if (!restored.length) {
        setMemoryFeedback(t(language, "brain.history.loadFailed", { reason: t(language, "brain.history.emptyConversation") }));
        return;
      }
      setConversationId(id);
      setMessages(restored);
      setMemoryFeedback(t(language, "brain.history.resumed"));
    } finally {
      setHistoryBusyId(null);
    }
  }, [historyBusyId, language, setConversationId, setMemoryFeedback, setMessages]);

  const deleteConversation = React.useCallback(async (id: string) => {
    if (historyBusyId) return;
    setHistoryBusyId(id);
    try {
      const result = await latticeApi.deleteConversation(id);
      if (!result.ok) {
        setMemoryFeedback(t(language, "brain.history.deleteFailed", { reason: result.error || "unavailable" }));
        return;
      }
      if (conversationId === id) resetConversation();
      setMemoryFeedback(t(language, "brain.history.deleted"));
      void qc.invalidateQueries({ queryKey: ["chatHistory"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    } finally {
      setHistoryBusyId(null);
    }
  }, [conversationId, historyBusyId, language, qc, resetConversation, setMemoryFeedback]);

  return {
    historyQ,
    pastConversations,
    historyBusyId,
    resetConversation,
    resumeConversation,
    deleteConversation,
  };
}
