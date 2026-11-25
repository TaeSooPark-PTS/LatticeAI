import { QueryClient } from "@tanstack/react-query";
import { useConversationSession } from "@/features/brain/conversationSession";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

export function clearScopedClientState() {
  queryClient.clear();
  useConversationSession.getState().resetConversation();
}
