import { create } from "zustand";
import type { Message } from "./types";

// The visible conversation must survive route changes: BrainHome unmounts
// whenever the user visits Capture/Library/System, so keeping messages in
// component state silently wiped the chat the Brain claims to remember.
type ConversationSessionState = {
  messages: Message[];
  conversationId: string | null;
  setMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
  setConversationId: (conversationId: string | null) => void;
  resetConversation: () => void;
};

export const useConversationSession = create<ConversationSessionState>((set) => ({
  messages: [],
  conversationId: null,
  setMessages: (updater) =>
    set((state) => ({
      messages: typeof updater === "function" ? updater(state.messages) : updater,
    })),
  setConversationId: (conversationId) => set({ conversationId }),
  resetConversation: () => set({ messages: [], conversationId: null }),
}));
