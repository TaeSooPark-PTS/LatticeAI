import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { PendingProposalsPanel } from "./PendingProposalsPanel";

/** Stub the proposal list; every field is optional so the gaps can be tested. */
function mockProposals(items: Array<Record<string, unknown>>) {
  return vi.spyOn(latticeApi, "proposals").mockResolvedValue({
    ok: true, status: 200, source: "api", data: { count: items.length, items },
  } as never);
}

async function openPanel() {
  await userEvent.click(screen.getByRole("button", { name: /변경 제안/ }));
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PendingProposalsPanel language="ko" />
    </QueryClientProvider>,
  );
}

describe("PendingProposalsPanel", () => {
  it("lists pending proposals with diff preview and approves on click", async () => {
    vi.spyOn(latticeApi, "proposals").mockResolvedValue({
      ok: true,
      status: 200,
      source: "api",
      data: {
        count: 1,
        items: [
          {
            id: "rp-1",
            title: "파일 수정 제안: site.html",
            summary: "기존 파일을 변경하는 작업이라 검토 후 적용됩니다.",
            kind: "file_update",
            payload: {
              path: "site.html",
              tier: "small",
              diff: ["--- a/site.html", "+++ b/site.html", "-<old>", "+<new>"],
            },
          },
        ],
      },
    } as never);
    // Approval goes through the review-queue surface so a base-changed
    // conflict comes back as a real 409 (see the conflict test below).
    const approveSpy = vi.spyOn(latticeApi, "approveReviewItem").mockResolvedValue({
      ok: true, status: 200, source: "api", data: { id: "rp-1", status: "approved" },
    } as never);

    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: /변경 제안/ }));

    await screen.findByText("site.html");
    expect(screen.getByText(/-<old>/)).toBeTruthy();
    expect(screen.getByText("작은 수정")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /승인하고 적용/ }));
    expect(approveSpy).toHaveBeenCalledWith("rp-1");
  });

  it("shows the conflict rebase flow when approval answers 409", async () => {
    vi.spyOn(latticeApi, "proposals").mockResolvedValue({
      ok: true,
      status: 200,
      source: "api",
      data: {
        count: 1,
        items: [
          {
            id: "rp-9",
            title: "파일 수정 제안: site.html",
            summary: "요약",
            kind: "file_update",
            payload: { path: "site.html", tier: "small", diff: [] },
          },
        ],
      },
    } as never);
    vi.spyOn(latticeApi, "approveReviewItem").mockResolvedValue({
      ok: false, status: 409, source: "unavailable", data: {}, error: "file_modified_since_proposal",
    } as never);

    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: /변경 제안/ }));
    await screen.findByText("site.html");
    await userEvent.click(screen.getByRole("button", { name: /승인하고 적용/ }));

    const note = await screen.findByTestId("proposal-conflict-note");
    expect(note.textContent).toContain("파일이 그 사이 변경되었습니다");
    expect(screen.getByRole("button", { name: /다시 읽어서 재적용/ })).toBeTruthy();
  });

  it("shows the empty state when nothing is pending", async () => {
    vi.spyOn(latticeApi, "proposals").mockResolvedValue({
      ok: true, status: 200, source: "api", data: { count: 0, items: [] },
    } as never);

    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: /변경 제안/ }));
    await screen.findByText("대기 중인 변경 제안이 없습니다");
  });

  it("rejects a proposal and refreshes both queues", async () => {
    mockProposals([{ id: "rp-2", title: "t", summary: "s", kind: "file_update", payload: { path: "a.md" } }]);
    const rejectSpy = vi.spyOn(latticeApi, "rejectProposal").mockResolvedValue({
      ok: true, status: 200, source: "api", data: {},
    } as never);

    renderPanel();
    await openPanel();
    await screen.findByText("a.md");
    await userEvent.click(screen.getByRole("button", { name: /거절/ }));
    await waitFor(() => expect(rejectSpy).toHaveBeenCalledWith("rp-2"));
  });

  it("leaves the item alone when approval fails for a reason other than a conflict", async () => {
    mockProposals([{ id: "rp-3", title: "t", summary: "s", kind: "file_update", payload: { path: "a.md" } }]);
    vi.spyOn(latticeApi, "approveReviewItem").mockResolvedValue({
      ok: false, status: 500, source: "unavailable", data: {}, error: "disk full",
    } as never);

    renderPanel();
    await openPanel();
    await screen.findByText("a.md");
    await userEvent.click(screen.getByRole("button", { name: /승인하고 적용/ }));
    // No rebase offer — the file did not drift, the write simply failed.
    await waitFor(() => expect(latticeApi.approveReviewItem).toHaveBeenCalled());
    expect(screen.queryByTestId("proposal-conflict-note")).toBeNull();
  });

  it("names a proposal by its title, and sizes it small, when the payload is bare", async () => {
    mockProposals([{ id: "rp-4", title: "파일 수정 제안", summary: "요약", kind: "file_update" }]);
    renderPanel();
    await openPanel();
    await screen.findByText("파일 수정 제안");
    expect(screen.getByText("작은 수정")).toBeTruthy();
  });

  it("labels a deletion by kind and a big edit by tier", async () => {
    mockProposals([
      { id: "rp-5", title: "삭제 제안", summary: "s", kind: "file_delete", payload: { path: "old.md" } },
      { id: "rp-6", title: "큰 수정 제안", summary: "s", kind: "file_update", payload: { path: "big.md", tier: "large" } },
    ]);
    renderPanel();
    await openPanel();
    await screen.findByText("old.md");
    expect(screen.getByText("삭제")).toBeTruthy();
    expect(screen.getByText("큰 수정")).toBeTruthy();
  });

  it("says the list could not be loaded, with the reason, and retries on demand", async () => {
    const spy = vi.spyOn(latticeApi, "proposals").mockResolvedValue({
      ok: false, status: 503, source: "unavailable", data: {}, error: "connection refused",
    } as never);

    renderPanel();
    await openPanel();
    await screen.findByRole("alert");
    // A failed load must not masquerade as "nothing pending".
    expect(screen.queryByText("대기 중인 변경 제안이 없습니다")).toBeNull();
    expect(screen.getByText("connection refused")).toBeTruthy();

    spy.mockResolvedValue({ ok: true, status: 200, source: "api", data: { count: 0, items: [] } } as never);
    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await screen.findByText("대기 중인 변경 제안이 없습니다");
  });

  it("still explains a failure that carried no message", async () => {
    vi.spyOn(latticeApi, "proposals").mockResolvedValue({
      ok: false, status: 500, source: "unavailable", data: {},
    } as never);

    renderPanel();
    await openPanel();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("변경 제안을 불러오지 못했어요");
    expect(alert.querySelector("small")).toBeNull();
  });
});
