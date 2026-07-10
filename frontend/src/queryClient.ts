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

/** Drop every identity/workspace-scoped client value in one synchronous boundary. */
export function clearScopedClientState() {
  // QueryClient.clear() destroys active queries, which also cancels their
  // in-flight requests. A late response from the previous scope therefore
  // cannot repopulate a shared query key.
  queryClient.clear();
  useConversationSession.getState().resetConversation();
}
