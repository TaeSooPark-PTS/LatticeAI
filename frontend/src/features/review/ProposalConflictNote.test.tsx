/**
 * The 409 recovery surface.
 *
 * When a change proposal is approved after its target file drifted, the
 * backend refuses — correctly — and this note is the only path forward the
 * person is offered. Everything that can go wrong with it is invisible on a
 * screenshot: the button that never disables, the failure that reports
 * "[object Object]", the caches that keep showing the retired proposal.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderPage } from "@/test/renderPage";
import { ProposalConflictNote } from "./ProposalConflictNote";
import { rebaseProposal } from "./proposalRebase";

vi.mock("./proposalRebase", () => ({ rebaseProposal: vi.fn() }));

const rebase = vi.mocked(rebaseProposal);

/** A promise this test resolves by hand, to observe the in-flight state. */
function deferred<T>() {
  let settle!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

beforeEach(() => {
  rebase.mockReset();
});

function render(onResolved?: (outcome: "rebased" | "already_applied") => void) {
  return renderPage(<ProposalConflictNote language="ko" itemId="rev-9" onResolved={onResolved} />);
}

describe("ProposalConflictNote", () => {
  it("explains what happened and offers the recovery", () => {
    render();
    const note = screen.getByTestId("proposal-conflict-note");
    expect(note.textContent).toContain("파일이 그 사이 변경되었습니다");
    expect(note.textContent).toContain("현재 파일을 다시 읽어");
    expect(screen.getByRole("button", { name: /다시 읽어서 재적용/ })).toBeEnabled();
  });

  it("re-stages the proposal, tells the caller, and refreshes every affected queue", async () => {
    rebase.mockResolvedValue("rebased");
    const onResolved = vi.fn();
    const { client } = render(onResolved);
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: /다시 읽어서 재적용/ }));

    await screen.findByRole("status");
    expect(rebase).toHaveBeenCalledWith("rev-9");
    expect(screen.getByRole("status").textContent).toContain("새 제안을 만들었어요");
    // The button is gone: there is nothing left to re-apply.
    expect(screen.queryByRole("button", { name: /다시 읽어서 재적용/ })).toBeNull();
    expect(onResolved).toHaveBeenCalledWith("rebased");
    const keys = invalidate.mock.calls.map((call) => String(call[0]?.queryKey));
    expect(keys).toEqual(["pendingProposals", "proposalCounts", "automationReviews", "reviewItems"]);
  });

  it("says so when the change had already landed", async () => {
    rebase.mockResolvedValue("already_applied");
    render();
    await userEvent.click(screen.getByRole("button", { name: /다시 읽어서 재적용/ }));
    expect((await screen.findByRole("status")).textContent).toContain("이미 같은 내용이 반영되어");
  });

  it("disables itself and says what it is doing while the re-read runs", async () => {
    const gate = deferred<"rebased">();
    rebase.mockReturnValue(gate.promise);
    render();

    await userEvent.click(screen.getByRole("button", { name: /다시 읽어서 재적용/ }));
    const busy = await screen.findByRole("button", { name: /현재 파일을 다시 읽는 중/ });
    expect(busy).toBeDisabled();

    gate.settle("rebased");
    await screen.findByRole("status");
  });

  it("reports why the re-apply failed, for an Error and for anything else", async () => {
    rebase.mockRejectedValue(new Error("파일을 읽지 못했습니다"));
    const { unmount } = render();
    await userEvent.click(screen.getByRole("button", { name: /다시 읽어서 재적용/ }));
    await waitFor(() =>
      expect(document.body.textContent).toContain("다시 적용을 준비하지 못했어요: 파일을 읽지 못했습니다"),
    );
    unmount();

    // A thrown non-Error must still read as a sentence, not "[object Object]".
    rebase.mockRejectedValue("network down");
    render();
    await userEvent.click(screen.getByRole("button", { name: /다시 읽어서 재적용/ }));
    await waitFor(() =>
      expect(document.body.textContent).toContain("다시 적용을 준비하지 못했어요: network down"),
    );
  });
});
