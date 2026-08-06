import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi, type ReviewItem } from "@/api/client";
import { fail, ok, renderPage } from "@/test/renderPage";
import { ReviewInbox } from "./ReviewInbox";

/**
 * The inbox header carries two filter strips. 10.6.x merged them onto one row
 * and gave each a visible caption, which is where two bugs entered:
 *
 *   - the captions were read from keys that did not exist. `t()` falls back to
 *     the key itself, never to a falsy value, so `t(k) || "Status"` rendered the
 *     literal string `review.status.title` on screen rather than the fallback.
 *   - two tablists on one row with no accessible name are indistinguishable to
 *     a screen reader; the captions have to name them.
 */

function reviewItem(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: "rev-1",
    status: "pending",
    effective_status: "pending",
    title: "검토 항목",
    summary: "요약",
    source: "workflow_run",
    kind: "release_review",
    payload: {},
    provenance: {},
    created_at: "2026-06-22T12:00:00Z",
    updated_at: "2026-06-22T12:00:00Z",
    ...overrides,
  } as ReviewItem;
}

const REVIEWS = ok({ items: [reviewItem()] });

type ApiOverrides = NonNullable<Parameters<typeof renderPage>[1]>["api"];

function render(api: ApiOverrides = {}) {
  return renderPage(<ReviewInbox />, {
    mode: "basic",
    api: { automationReviews: REVIEWS, proposalCounts: ok({ pending: 0 }), ...api },
  });
}

/** Wait for the queue to have replaced its loading panel with a card. */
async function findCard(title = "검토 항목") {
  return screen.findByRole("heading", { name: title });
}

describe("ReviewInbox", () => {
  it("renders the filter captions as copy, never as raw i18n keys", async () => {
    render();
    await waitFor(() => expect(screen.getAllByRole("tablist").length).toBe(2));
    // The exact failure this guards: `t()` returning the key string.
    expect(document.body.textContent).not.toContain("review.status.title");
    expect(document.body.textContent).not.toContain("review.source.title");
    expect(screen.getByText("상태")).toBeTruthy();
    expect(screen.getByText("출처")).toBeTruthy();
  });

  it("gives each filter strip its own accessible name", async () => {
    render();
    await waitFor(() => expect(screen.getAllByRole("tablist").length).toBe(2));
    expect(screen.getByRole("tablist", { name: "상태" })).toBeTruthy();
    expect(screen.getByRole("tablist", { name: "출처" })).toBeTruthy();
  });

  it("narrows the query by status and source, and drops 'all' from the request", async () => {
    render();
    await findCard();
    expect(latticeApi.automationReviews).toHaveBeenLastCalledWith({ status: "pending" });

    await userEvent.click(screen.getByRole("tab", { name: "전체" }));
    await userEvent.click(screen.getByRole("tab", { name: "변경 제안" }));
    await waitFor(() =>
      // "all" is the absence of a filter, not a value the API is asked for.
      expect(latticeApi.automationReviews).toHaveBeenLastCalledWith({ source: "change_proposal" }),
    );
  });

  it("counts pending change proposals in a badge of its own", async () => {
    render({ proposalCounts: ok({ pending: 3 }) });
    expect((await screen.findByTestId("proposal-count-badge")).textContent).toContain("3");
    expect(screen.getByText("연결됨")).toBeTruthy();
  });
});

describe("ReviewInbox when the queue is unreachable", () => {
  it("explains an outright failure without inventing an empty queue", async () => {
    render({ automationReviews: () => Promise.reject(new Error("offline")) });
    await screen.findByText("검토함을 불러올 수 없습니다.");
    expect(screen.getByText("지금은 검토 대기열을 사용할 수 없습니다.")).toBeTruthy();
    // No connection badge at all: there is no envelope to describe.
    expect(screen.queryByText("연결됨")).toBeNull();
    expect(screen.queryByText("사용 불가")).toBeNull();
  });

  it("demotes the backend's own message under the friendly one", async () => {
    render({ automationReviews: fail("database is locked", { items: [] }) });
    await screen.findByText("검토함을 불러올 수 없습니다.");
    expect(screen.getByText("database is locked")).toBeTruthy();
    expect(screen.getByText("사용 불가")).toBeTruthy();
  });

  it("tells the reader which empty queue they are looking at", async () => {
    render({ automationReviews: ok({ items: [] }) });
    await screen.findByText("검토할 항목 없음");
    expect(screen.getByText(/자동화가 검토 대기열을 사용하면/)).toBeTruthy();

    await userEvent.click(screen.getByRole("tab", { name: "일시 중지" }));
    await screen.findByText(/일시 중지된 항목은 해제되거나/);
  });
});

describe("ReviewInbox actions", () => {
  it("approves an item, clears its feedback and refreshes every affected queue", async () => {
    const { client } = render();
    await findCard();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await userEvent.click(screen.getByRole("button", { name: "승인" }));

    await waitFor(() => expect(latticeApi.approveReviewItem).toHaveBeenCalledWith("rev-1"));
    await waitFor(() =>
      expect(invalidate.mock.calls.map((call) => String(call[0]?.queryKey))).toEqual([
        "automationReviews",
        "proposalCounts",
        "pendingProposals",
      ]),
    );
  });

  it("dismisses an ordinary item through the review surface", async () => {
    render();
    await findCard();
    await userEvent.click(screen.getByRole("button", { name: "기각" }));
    await waitFor(() => expect(latticeApi.dismissReviewItem).toHaveBeenCalledWith("rev-1"));
    expect(latticeApi.rejectProposal).not.toHaveBeenCalled();
  });

  it("snoozes for a day", async () => {
    render();
    await findCard();
    await userEvent.click(screen.getByRole("button", { name: "하루 일시 중지" }));
    await waitFor(() => expect(latticeApi.snoozeReviewItem).toHaveBeenCalled());
    expect(String(vi.mocked(latticeApi.snoozeReviewItem).mock.calls[0][1])).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it("unsnoozes an item that is already resting", async () => {
    render({ automationReviews: ok({ items: [reviewItem({ effective_status: "snoozed" })] }) });
    await findCard();
    await userEvent.click(screen.getByRole("button", { name: /일시 중지 해제/ }));
    await waitFor(() => expect(latticeApi.unsnoozeReviewItem).toHaveBeenCalledWith("rev-1"));
  });

  it("rejects a change proposal through the proposal surface, with and without a reason", async () => {
    // The rejection reason belongs in the proposal's provenance (audit trail),
    // which the generic dismiss endpoint has nowhere to put.
    const proposal = ok({ items: [reviewItem({ source: "change_proposal", kind: "file_write" })] });
    render({ automationReviews: proposal });
    await findCard();

    await userEvent.type(screen.getByLabelText(/사유/), "지금은 아님");
    await userEvent.click(screen.getByRole("button", { name: "거절" }));
    await waitFor(() => expect(latticeApi.rejectProposal).toHaveBeenCalledWith("rev-1", "지금은 아님"));

    vi.mocked(latticeApi.rejectProposal).mockClear();
    await userEvent.clear(screen.getByLabelText(/사유/));
    await userEvent.click(screen.getByRole("button", { name: "거절" }));
    await waitFor(() => expect(latticeApi.rejectProposal).toHaveBeenCalledWith("rev-1", ""));
  });

  it("names the run in the feedback", async () => {
    render({ runNowReviewItem: ok(reviewItem({ payload: { last_run_id: "run-9" } })) });
    await findCard();
    await userEvent.click(screen.getByRole("button", { name: "지금 실행" }));
    await screen.findByText(/실행됨 · run-9/);
  });

  it("says 'regenerated' when the item had already run once", async () => {
    render({
      automationReviews: ok({ items: [reviewItem({ payload: { last_run_id: "run-1" } })] }),
      // This run reports its id only through the provenance.
      runNowReviewItem: ok(reviewItem({ payload: {}, provenance: { run_id: "run-10" } })),
    });
    await findCard();
    await userEvent.click(screen.getByRole("button", { name: "지금 실행" }));
    await screen.findByText(/다시 생성됨 · run-10/);
  });

  it("still reports a run that came back without any run id", async () => {
    render({ runNowReviewItem: ok(reviewItem({ payload: undefined, provenance: undefined })) });
    await findCard();
    await userEvent.click(screen.getByRole("button", { name: "지금 실행" }));
    await waitFor(() => expect(screen.getAllByText("실행됨").length).toBeGreaterThan(0));
  });

  it("reports a failed action in plain words, with the raw detail demoted", async () => {
    render({ approveReviewItem: fail("workflow engine offline", {}, 503) });
    await findCard();
    await userEvent.click(screen.getByRole("button", { name: "승인" }));
    await screen.findByText(/승인 또는 기각 전까지/);
    // Once beside the button that failed, once as the card's demoted detail.
    expect(screen.getAllByText("workflow engine offline")).toHaveLength(2);
  });

  it("keeps the friendly message when the backend sent no detail at all", async () => {
    render({
      approveReviewItem: { ok: false, status: 500, source: "unavailable", data: {} },
    });
    await findCard();
    await userEvent.click(screen.getByRole("button", { name: "승인" }));
    await screen.findByText(/승인 또는 기각 전까지/);
    expect(document.body.textContent).not.toContain("undefined");
  });

  it("offers the rebase recovery when a proposal approval hits the 409 guard", async () => {
    render({
      automationReviews: ok({ items: [reviewItem({ source: "change_proposal", kind: "file_write" })] }),
      approveReviewItem: {
        ok: false,
        status: 409,
        source: "unavailable",
        data: {},
        error: "file_modified_since_proposal",
      },
    });
    await findCard();
    await userEvent.click(screen.getByRole("button", { name: "승인하고 적용" }));

    const note = await screen.findByTestId("proposal-conflict-note");
    expect(note.textContent).toContain("파일이 그 사이 변경되었습니다");
  });
});
