import { create } from "zustand";
import type { Message } from "./types";

// The visible conversation must survive route changes: BrainHome unmounts
// whenever the user visits Capture/Library/System, so keeping messages in
// component state silently wiped the chat the Brain claims to remember.
type ConversationSessionState = {
  messages: Message[];
  conversationId: string | null;
  // Per-conversation override: when the dial allows cloud, the person can
  // keep this turn on the machine. Reset with the conversation, not the route.
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
