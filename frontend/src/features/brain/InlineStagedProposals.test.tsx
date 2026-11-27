import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n/brain";
import "@/i18n/workspace";
import { BrainHome } from "./BrainHome";
import { useConversationSession } from "./conversationSession";
import { InlineStagedProposals } from "./InlineStagedProposals";
import { useAppStore } from "@/store/appStore";
import { fail, ok, renderPage } from "@/test/renderPage";

describe("InlineStagedProposals & Chat Insight Promotion", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState({ pendingChatPrompt: null, language: "ko" });
    useConversationSession.setState({ messages: [], conversationId: null });
  });

  it("renders nothing when proposals list is empty or unavailable", async () => {
    const empty = renderPage(<InlineStagedProposals language="ko" />, {
      api: { proposals: ok({ items: [] }) },
    });
    await waitFor(() => expect(screen.queryByTestId("inline-staged-proposals")).toBeNull());
    empty.unmount();

    renderPage(<InlineStagedProposals language="ko" />, {
      api: { proposals: fail("offline", 503) },
    });
    await waitFor(() => expect(screen.queryByTestId("inline-staged-proposals")).toBeNull());
  });

  it("displays staged proposals with diff preview and handles approve and reject outcomes", async () => {
    const approve = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 500, source: "unavailable", data: {} })
      .mockResolvedValueOnce({ ok: true, status: 200, source: "live", data: {} });
    const reject = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 500, source: "unavailable", data: {} })
      .mockResolvedValueOnce({ ok: true, status: 200, source: "live", data: {} });

    const proposals = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: {
          items: [
            {
              id: "p1",
              title: "Update config",
              summary: "Adjust timeout",
              kind: "file_write",
              payload: { path: "src/config.ts", diff: ["--- a/src/config.ts", "+++ b/src/config.ts", "+timeout = 30"] },
            },
            {
              id: "p2",
              title: "Fallback title item",
              summary: "",
              kind: "note",
            },
          ],
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: {
          items: [
            {
              id: "p2",
              title: "Fallback title item",
              summary: "",
              kind: "note",
            },
          ],
        },
      })
      .mockResolvedValue({ ok: true, status: 200, source: "live", data: { items: [] } });

    renderPage(<InlineStagedProposals language="en" />, {
      api: {
        proposals,
        approveReviewItem: approve,
        rejectProposal: reject,
      },
    });

    expect(await screen.findByTestId("inline-staged-proposals")).toBeTruthy();
    expect(screen.getByText("src/config.ts")).toBeTruthy();
    expect(screen.getByText("Adjust timeout")).toBeTruthy();
    expect(screen.getByText("Fallback title item")).toBeTruthy();

    await userEvent.click(screen.getByTestId("inline-proposal-approve-p1"));
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("status")).toBeNull();

    await userEvent.click(screen.getByTestId("inline-proposal-approve-p1"));
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("status").textContent).toContain("Approved and applied");

    await userEvent.click(screen.getByTestId("inline-proposal-reject-p2"));
    await waitFor(() => expect(reject).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByTestId("inline-proposal-reject-p2"));
    await waitFor(() => expect(reject).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("status").textContent).toContain("Rejected the change proposal.");
  });

  it("populates BrainHome draft from pendingChatPrompt, renders InlineStagedProposals on proposed agent steps, and promotes assistant insights via Save-to-Brain", async () => {
    const ingestNote = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { created_memories: 1, created_entities: 1, node_ids: ["mem-1"] },
    });
    useAppStore.getState().setPendingChatPrompt("그래프 연결 맥락 정리해줘");
    useConversationSession.setState({
      messages: [
        { role: "user", content: "요약해줘" },
        {
          role: "assistant",
          content: "핵심 아키텍처 결정 요약입니다.",
          agentSteps: [{ event: "proposed", phase: "execute", action: "write_file", path: "NOTES.md" }],
        },
      ],
    });

    renderPage(
      <BrainHome brainState="idle" intensity={0.6} onBrainChange={() => {}} />,
      {
        api: {
          ingestNote,
          proposals: ok({
            items: [{ id: "prop-9", title: "NOTES.md", summary: "Stage summary", kind: "file_write" }],
          }),
        },
      },
    );

    await waitFor(() => expect(useAppStore.getState().pendingChatPrompt).toBeNull());
    expect((screen.getByLabelText("질문하거나, 자료를 붙여 넣거나, 할 일을 적어보세요") as HTMLTextAreaElement).value).toBe("그래프 연결 맥락 정리해줘");

    expect(await screen.findByTestId("inline-proposal-prop-9")).toBeTruthy();

    const saveBtn = screen.getByTestId("message-save-brain") as HTMLButtonElement;
    expect(saveBtn.textContent).toContain("Brain에 기억하기");
    await userEvent.click(saveBtn);
    await waitFor(() => expect(ingestNote).toHaveBeenCalled());
    await waitFor(() => expect(saveBtn.textContent).toContain("Brain에 저장됨"));
    expect(saveBtn.disabled).toBe(true);
  });
});
