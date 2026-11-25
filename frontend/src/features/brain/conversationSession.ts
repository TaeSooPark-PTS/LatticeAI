import { create } from "zustand";
import type { Message } from "./types";

type ConversationSessionState = {
  messages: Message[];
  conversationId: string | null;
  preferLocalOnly: boolean;
  setMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
  setConversationId: (conversationId: string | null) => void;
  setPreferLocalOnly: (preferLocalOnly: boolean) => void;
  resetConversation: () => void;
};

export const useConversationSession = create<ConversationSessionState>((set) => ({
  messages: [],
  conversationId: null,
  preferLocalOnly: false,
  setMessages: (updater) =>
    set((state) => ({
      messages: typeof updater === "function" ? updater(state.messages) : updater,
    })),
  setConversationId: (conversationId) => set({ conversationId }),
  setPreferLocalOnly: (preferLocalOnly) => set({ preferLocalOnly }),
  resetConversation: () => set({ messages: [], conversationId: null, preferLocalOnly: false }),
}));
