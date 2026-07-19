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
    const approveSpy = vi.spyOn(latticeApi, "approveProposal").mockResolvedValue({
      ok: true, status: 200, source: "api", data: { applied: true },
    } as never);

    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: /변경 제안/ }));

    await screen.findByText("site.html");
    expect(screen.getByText(/-<old>/)).toBeTruthy();
    expect(screen.getByText("작은 수정")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /승인하고 적용/ }));
    expect(approveSpy).toHaveBeenCalledWith("rp-1");
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
