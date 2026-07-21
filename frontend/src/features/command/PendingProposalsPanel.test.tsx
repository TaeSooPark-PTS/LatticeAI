import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { PendingProposalsPanel } from "./PendingProposalsPanel";

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
});
