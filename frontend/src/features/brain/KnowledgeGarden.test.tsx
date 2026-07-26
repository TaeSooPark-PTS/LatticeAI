import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { KnowledgeGardenPanel, parseGarden } from "./KnowledgeGarden";

const PAYLOAD = {
  available: true,
  beds: {
    recent: { count: 2, items: [{ id: "n1", title: "이번 주 회의", type: "Document", updated_at: "2026-07-27" }] },
    contradictions: { count: 1, items: [{ id: "c1", summary: "예산이 다릅니다" }] },
    stale: { count: 0, items: [] },
    frequent: { count: 1, items: [{ id: "n-hub", title: "예산", type: "Concept", degree: 12 }] },
  },
};

function mockGarden(payload: unknown, ok = true) {
  return vi.spyOn(latticeApi, "brainGarden").mockResolvedValue({
    ok, status: ok ? 200 : 500, source: "live", data: payload,
  } as never);
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <KnowledgeGardenPanel language="ko" />
    </QueryClientProvider>,
  );
}

describe("parseGarden", () => {
  it("keeps the four beds in a stable order", () => {
    expect(parseGarden(PAYLOAD).beds.map((bed) => bed.id)).toEqual([
      "recent", "contradictions", "stale", "frequent",
    ]);
  });

  it("drops rows that have no id or no label", () => {
    const parsed = parseGarden({
      available: true,
      beds: { recent: { items: [{ id: "a", title: "ok" }, { id: "" }, { title: "no id" }, 7] } },
    });
    const recent = parsed.beds.find((bed) => bed.id === "recent")!;
    expect(recent.items.map((item) => item.id)).toEqual(["a"]);
  });

  it("shows degree as the detail for the frequent bed", () => {
    const frequent = parseGarden(PAYLOAD).beds.find((bed) => bed.id === "frequent")!;
    expect(frequent.items[0].detail).toBe("12");
  });

  it("reports unavailable gardens instead of inventing beds", () => {
    const parsed = parseGarden({ available: false, beds: {} });
    expect(parsed.available).toBe(false);
    expect(parsed.beds.every((bed) => bed.items.length === 0 && bed.count === 0)).toBe(true);
    expect(parseGarden(null).available).toBe(false);
  });
});

describe("KnowledgeGardenPanel", () => {
  it("loads only when the gardener actually opens it", async () => {
    const spy = mockGarden(PAYLOAD);
    renderPanel();
    expect(spy).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText("내 지식 정원"));
    await waitFor(() => expect(screen.getByTestId("garden-bed-recent")).toBeTruthy());
    expect(spy).toHaveBeenCalled();
  });

  it("renders every bed, including the empty one, with an honest message", async () => {
    mockGarden(PAYLOAD);
    renderPanel();
    await userEvent.click(screen.getByText("내 지식 정원"));
    await waitFor(() => expect(screen.getByTestId("garden-bed-stale")).toBeTruthy());
    expect(screen.getByText("이번 주 회의")).toBeTruthy();
    expect(screen.getByText("예산이 다릅니다")).toBeTruthy();
    expect(screen.getByText("오래 방치된 자료가 없어요.")).toBeTruthy();
  });

  it("says so when there is no garden yet", async () => {
    mockGarden({ available: false, beds: {} });
    renderPanel();
    await userEvent.click(screen.getByText("내 지식 정원"));
    await waitFor(() =>
      expect(screen.getByText("아직 정원을 볼 수 없어요. 자료를 조금 더 모아 보세요.")).toBeTruthy(),
    );
  });
});
