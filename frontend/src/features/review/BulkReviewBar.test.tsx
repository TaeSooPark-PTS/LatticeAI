import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { latticeApi, type ReviewItem } from "@/api/client";
import { fail, ok, renderPage, type RenderPageOptions } from "@/test/renderPage";
import { ReviewInbox } from "./ReviewInbox";

function item(id: string, overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id,
    status: "pending",
    effective_status: "pending",
    title: `제목 ${id}`,
    summary: "",
    source: "workflow_run",
    kind: "suggestion",
    payload: {},
    provenance: {},
    ...overrides,
  } as ReviewItem;
}

const ITEMS = [
  item("a"),
  item("b", { effective_status: "snoozed" }),
  item("c"),
  item("d"),
  item("p", { source: "change_proposal" }),
  item("done", { effective_status: "approved" }),
];

function render(api: RenderPageOptions["api"] = {}) {
  return renderPage(<ReviewInbox />, {
    mode: "basic",
    api: { automationReviews: ok({ items: ITEMS }), proposalCounts: ok({ pending: 0 }), ...api },
  });
}

const box = (id: string) => screen.getByRole("checkbox", { name: `제목 ${id} 선택` });

describe("bulk review", () => {
  it("never selects change proposals or decided items", async () => {
    render();
    await screen.findByText("제목 a");
    expect(screen.queryByRole("checkbox", { name: "제목 p 선택" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "제목 done 선택" })).toBeNull();
    expect(screen.getByText("변경 제안은 파일 차이를 확인해야 해서 하나씩 결정합니다.")).toBeTruthy();
    await userEvent.click(screen.getByRole("checkbox", { name: "모두 선택" }));
    expect(screen.getByText("4개 선택")).toBeTruthy();
    await userEvent.click(screen.getByRole("checkbox", { name: "모두 선택" }));
    expect(screen.getByText("0개 선택")).toBeTruthy();
  });

  it("asks before approving and lists what failed", async () => {
    render({
      bulkReview: ok({
        requested: 4,
        succeeded: 1,
        failed: 3,
        results: [
          { id: "a", status: "ok" },
          { id: "b", status: "conflict", detail: "이미 처리됨" },
          { id: "c", status: "not_found", detail: null },
          { id: "zz", status: "failed" },
        ],
      }),
    });
    await screen.findByText("제목 a");
    await userEvent.click(box("a"));
    await userEvent.click(box("c"));
    await userEvent.click(box("a"));
    await userEvent.click(box("a"));
    await userEvent.click(screen.getByRole("button", { name: "선택 항목 승인" }));
    expect(latticeApi.bulkReview).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "승인 확정" }));
    await userEvent.click(screen.getByRole("button", { name: "취소" }));
    await userEvent.click(screen.getByRole("button", { name: "선택 항목 승인" }));
    await userEvent.click(screen.getByRole("button", { name: "승인 확정" }));
    expect(latticeApi.bulkReview).toHaveBeenCalledWith("approve", ["c", "a"]);
    expect(await screen.findByText("성공 1개 · 실패 3개")).toBeTruthy();
    expect(screen.getByText("제목 b: 이미 처리됨")).toBeTruthy();
    expect(screen.getByText("제목 c: 항목을 찾을 수 없어요")).toBeTruthy();
    expect(screen.getByText("zz: 처리하지 못했어요")).toBeTruthy();
  });

  it("drops a pending confirmation when the selection empties", async () => {
    render();
    await screen.findByText("제목 a");
    await userEvent.click(box("a"));
    await userEvent.click(screen.getByRole("button", { name: "선택 항목 승인" }));
    await userEvent.click(box("a"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "승인 확정" })).toBeNull());
  });

  it("dismisses at once and reports full success", async () => {
    render({ bulkReview: ok({ requested: 1, succeeded: 1, failed: 0, results: [{ id: "a", status: "ok" }] }) });
    await screen.findByText("제목 a");
    await userEvent.click(box("a"));
    await userEvent.click(screen.getByRole("button", { name: "선택 항목 기각" }));
    expect(latticeApi.bulkReview).toHaveBeenCalledWith("dismiss", ["a"]);
    expect(await screen.findByText("성공 1개 · 실패 0개")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("0개 선택")).toBeTruthy());
  });

  it("keeps the selection when the request itself fails", async () => {
    const answers = [fail("서버 거절", {}), fail("", {})];
    render({ bulkReview: () => Promise.resolve(answers.shift()) });
    await screen.findByText("제목 a");
    await userEvent.click(box("a"));
    await userEvent.click(screen.getByRole("button", { name: "선택 항목 기각" }));
    expect(await screen.findByText("서버 거절")).toBeTruthy();
    expect(screen.getByText("1개 선택")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "선택 항목 기각" }));
    await waitFor(() => expect(screen.queryByText("서버 거절")).toBeNull());
    expect(screen.getByRole("alert").textContent).toBeTruthy();
  });
});
