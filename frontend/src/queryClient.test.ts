
import { describe, expect, it } from "vitest";

import { useConversationSession } from "@/features/brain/conversationSession";
import { clearScopedClientState, queryClient } from "./queryClient";

describe("clearScopedClientState", () => {
  it("drops cached query data and the active conversation together", () => {
    queryClient.setQueryData(["models"], { items: ["private-model"] });
    useConversationSession.setState({
      conversationId: "conv-1",
      messages: [{ role: "user", content: "비밀" }] as never,
    });
    expect(queryClient.getQueryCache().getAll().length).toBeGreaterThan(0);

    clearScopedClientState();

    expect(queryClient.getQueryData(["models"])).toBeUndefined();
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(useConversationSession.getState().conversationId).toBeNull();
    expect(useConversationSession.getState().messages).toEqual([]);
  });

  it("ships a client that keeps data briefly and retries once", () => {
    const defaults = queryClient.getDefaultOptions().queries;
    expect(defaults?.staleTime).toBe(15_000);
    expect(defaults?.refetchOnWindowFocus).toBe(false);
    expect(defaults?.retry).toBe(1);
  });
});
