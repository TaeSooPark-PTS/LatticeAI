import { beforeEach, describe, expect, it } from "vitest";

import { useConversationSession } from "./conversationSession";

describe("conversation session", () => {
  beforeEach(() => useConversationSession.getState().resetConversation());

  it("supports functional message updates without losing the active conversation", () => {
    const session = useConversationSession.getState();
    session.setConversationId("conversation-1");
    session.setMessages([{ role: "user", content: "Hello" }]);
    session.setMessages((messages) => [...messages, { role: "assistant", content: "Hi" }]);

    expect(useConversationSession.getState()).toMatchObject({
      conversationId: "conversation-1",
      messages: [
        { role: "user", content: "Hello" },
        { role: "assistant", content: "Hi" },
      ],
    });
  });

  it("clears messages and conversation identity at the same boundary", () => {
    useConversationSession.getState().setConversationId("conversation-2");
    useConversationSession.getState().setMessages([{ role: "user", content: "Private" }]);

    useConversationSession.getState().resetConversation();

    expect(useConversationSession.getState().conversationId).toBeNull();
    expect(useConversationSession.getState().messages).toEqual([]);
  });
});
