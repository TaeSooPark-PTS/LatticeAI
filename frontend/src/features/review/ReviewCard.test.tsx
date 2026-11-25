import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ApiResult, ReviewItem } from "@/api/client";
import { ok, renderPage } from "@/test/renderPage";
import { ProposalDiff, ReviewCard } from "./ReviewCard";
import type { ReviewFeedback } from "./reviewHelpers";


function item(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: "rev-1",
    status: "pending",
    effective_status: "pending",
    title: "테스트 검토 항목",
    summary: "요약",
    source: "chat_followup",
    kind: "followup",
    payload: {},
    provenance: {},
    created_at: "2026-06-22T12:00:00Z",
    updated_at: "2026-06-22T12:00:00Z",
    ...overrides,
  } as ReviewItem;
}

const PROPOSAL = item({
  id: "rev-proposal",
  source: "change_proposal",
  kind: "file_write",
  payload: {
    path: "README.md",
    tier: "small",
    diff: ["--- a/README.md", "+++ b/README.md", "+새 줄", "-옛 줄"],
  },
  provenance: { risk: "low", change_class: "docs", tool: "write_file", proposed_by: "Brain" },
} as Partial<ReviewItem>);

function noop() {
  return Promise.resolve(ok({}) as unknown as ApiResult<ReviewItem>);
}

function render(reviewItem: ReviewItem, mode: "basic" | "advanced" = "basic") {
  return renderPage(<ReviewCard item={reviewItem} onAction={vi.fn(noop)} />, { mode });
}

function renderWithAction(reviewItem: ReviewItem, mode: "basic" | "advanced" = "advanced") {
  const onAction = vi.fn(noop);
  const result = renderPage(<ReviewCard item={reviewItem} onAction={onAction} />, { mode });
  return { ...result, onAction };
}

function renderFeedback(reviewItem: ReviewItem, feedback: ReviewFeedback) {
  return renderPage(<ReviewCard item={reviewItem} feedback={feedback} onAction={vi.fn(noop)} />, {
    mode: "basic",
  });
}

describe("ReviewCard", () => {
  it("never emits a <main> landmark, because a page renders many cards", () => {
    render(PROPOSAL, "advanced");
    expect(document.querySelectorAll("main").length).toBe(0);
  });

  it("scopes its header bar so it is not a page banner", () => {
    const { container } = render(PROPOSAL, "advanced");
    const headers = container.querySelectorAll("header, footer");
    expect(headers.length).toBeGreaterThan(0);
    for (const el of headers) {
      expect(el.closest("article, aside, main, nav, section")).not.toBeNull();
    }
  });

  it("makes the item title the card's heading, not the word 결정하기", () => {
    render(PROPOSAL, "advanced");
    const titleHeading = screen.getByRole("heading", { name: "테스트 검토 항목" });
    const decisionHeading = screen.getByRole("heading", { name: "결정하기" });
    expect(titleHeading.tagName).toBe("H3");
    expect(decisionHeading.tagName).toBe("H4");
  });

  it("names the card by its title so each item is one navigable unit", () => {
    render(PROPOSAL, "advanced");
    expect(screen.getByRole("article", { name: "테스트 검토 항목" })).toBeTruthy();
  });

  it("shows the decision heading and filter copy as text, not as i18n keys", () => {
    render(PROPOSAL, "advanced");
    expect(document.body.textContent).not.toMatch(/review\.[a-z]+\.[a-zA-Z]+/);
    expect(screen.getByRole("heading", { name: "결정하기" })).toBeTruthy();
  });

  it("names the approve/reject cluster as a group so the label is announced", () => {
    render(PROPOSAL, "advanced");
    expect(screen.getByRole("group", { name: "검토 작업" })).toBeTruthy();
  });

  it("keeps both decisions reachable, and the reject reason with them", () => {
    render(PROPOSAL, "advanced");
    expect(screen.getByRole("button", { name: /승인/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /거절|기각/ })).toBeTruthy();
    expect(screen.getByLabelText(/사유/)).toBeTruthy();
  });

  it("splits into two columns only when there is evidence to show", () => {
    const { container } = render(PROPOSAL, "advanced");
    expect(container.querySelector(".md\\:grid-cols-12")).toBeTruthy();
  });

  it("stays single-column when the item carries no evidence at all", () => {
    const { container } = render(item(), "basic");
    expect(container.querySelector(".md\\:grid-cols-12")).toBeNull();
    expect(container.querySelector(".md\\:col-span-5")).toBeNull();
    expect(screen.getByRole("group", { name: "검토 작업" })).toBeTruthy();
  });

  it("renders the diff for a proposal", () => {
    render(PROPOSAL, "advanced");
    expect(document.body.textContent).toContain("README.md");
    expect(document.body.textContent).toContain("새 줄");
  });
});

describe("ProposalDiff", () => {
  it("renders nothing at all when there is no diff to show", () => {
    const { container } = renderPage(<ProposalDiff language="ko" diff={[]} />);
    expect(container.querySelector("figure")).toBeNull();
  });

  it("caps the preview and says how many lines it is hiding", () => {
    const diff = ["--- a/x.md", "+++ b/x.md", "", ...Array.from({ length: 30 }, (_, i) => `+줄 ${i}`)];
    renderPage(<ProposalDiff language="ko" diff={diff} />);
    expect(screen.getByText("9줄 더 있음")).toBeTruthy();
    expect(document.querySelector(".pending-proposal-diff code")).toBeNull();
    const rows = document.querySelectorAll(".pending-proposal-diff span");
    expect(rows.length).toBe(24);
    expect(rows[2]!.textContent).toBe(" ");
  });
});

describe("ReviewCard evidence panel", () => {
  it("labels a deletion proposal by kind and a big edit by tier", () => {
    render(
      item({
        source: "change_proposal",
        kind: "file_delete",
        payload: { path: "old.md", tier: "large", diff: ["--- a/old.md"] },
      }),
      "basic",
    );
    expect(screen.getByText("삭제")).toBeTruthy();
    expect(screen.queryByText("큰 수정")).toBeNull();

    render(item({ source: "change_proposal", payload: { tier: "large" } }), "basic");
    expect(screen.getByText("큰 수정")).toBeTruthy();
  });

  it("survives an item with no payload, provenance or summary at all", () => {
    const bare = item({ payload: undefined, provenance: undefined, summary: "" });
    render(bare, "advanced");
    expect(screen.getByRole("heading", { name: "테스트 검토 항목" })).toBeTruthy();
    expect(screen.queryByText("요약")).toBeNull();
    expect(document.body.textContent).not.toContain("path");
  });

  it("shows a lone risk badge, and a lone change-class badge, without the other", () => {
    render(item({ provenance: { risk: "write" } }), "basic");
    expect(screen.getByText("파일을 수정함")).toBeTruthy();
    expect(document.body.textContent).not.toContain("사용 도구");
    expect(document.body.textContent).not.toContain("제안한 주체");

    render(item({ provenance: { change_class: "mutation" } }), "basic");
    expect(screen.getByText("기존 내용 수정")).toBeTruthy();
  });

  it("shows the tool and proposer lines with no governance badges above them", () => {
    const { container } = render(
      item({ provenance: { tool: "write_file", proposed_by: "Brain" } }),
      "basic",
    );
    expect(screen.getByText("사용 도구")).toBeTruthy();
    expect(screen.getByText("제안한 주체")).toBeTruthy();
    expect(container.querySelector(".text-\\[10px\\]")).toBeNull();
  });

  it("explains a snoozed item and offers only the unsnooze action", async () => {
    const snoozed = item({
      effective_status: "snoozed",
      snoozed_until: "2026-06-23T12:00:00Z",
      source: "workflow_run",
    });
    const { onAction } = renderWithAction(snoozed, "basic");

    expect(document.body.textContent).toContain("까지 일시 중지");
    expect(screen.queryByRole("button", { name: "하루 일시 중지" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /일시 중지 해제/ }));
    expect(onAction).toHaveBeenCalledWith(snoozed, "unsnooze");
  });
});

describe("ReviewCard decision panel", () => {
  it("names an agent follow-up as an agent task and hides run-now", () => {
    render(item({ source: "agent_followup" }), "basic");
    expect(document.body.textContent).toContain("Agent가 실행 결과에서 뽑은");
    expect(screen.queryByRole("button", { name: "지금 실행" })).toBeNull();
  });

  it("says 'regenerated' rather than 'executed' for an item that already ran", () => {
    render(item({ source: "workflow_run", payload: { last_run_id: "run-1" } }), "basic");
    expect(screen.getByRole("button", { name: "지금 실행" })).toBeTruthy();
  });

  it("shows a settled item's status instead of decisions", () => {
    render(item({ status: "approved", effective_status: "approved" }), "basic");
    expect(screen.getAllByText("승인됨")).toHaveLength(2);
    expect(screen.queryByRole("group", { name: "검토 작업" })).toBeNull();
    expect(screen.queryByRole("button", { name: "승인" })).toBeNull();
  });

  it("renders a Rollback badge on auto-staged reorganization undo proposals and triggers 1-click undo on settled cards", async () => {
    render(
      item({
        source: "change_proposal",
        kind: "folder_reorganization",
        payload: { is_undo: true, root: "inbox", moves: [] },
      }),
      "basic",
    );
    expect(screen.getByText("원복 제안")).toBeTruthy();

    const approvedReorg = item({
      id: "review-forward-1",
      source: "change_proposal",
      kind: "folder_reorganization",
      status: "approved",
      effective_status: "approved",
      provenance: { undo_proposal_id: "review-undo-99" },
    });
    const { onAction } = renderWithAction(approvedReorg, "basic");
    await userEvent.click(screen.getByRole("button", { name: "원래 위치로 되돌리기" }));
    await waitFor(() =>
      expect(onAction).toHaveBeenCalledWith(
        expect.objectContaining({ id: "review-undo-99", status: "pending", effective_status: "pending" }),
        "approve",
      ),
    );
  });

  it("passes the typed reason along when a proposal is rejected", async () => {
    const { onAction } = renderWithAction(PROPOSAL);
    await userEvent.type(screen.getByLabelText(/사유/), "다시 검토 필요");
    await userEvent.click(screen.getByRole("button", { name: "거절" }));
    await waitFor(() =>
      expect(onAction).toHaveBeenCalledWith(PROPOSAL, "dismiss", false, "다시 검토 필요"),
    );
  });

  it("approves a proposal without any extra argument", async () => {
    const { onAction } = renderWithAction(PROPOSAL);
    await userEvent.click(screen.getByRole("button", { name: "승인하고 적용" }));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith(PROPOSAL, "approve"));
  });

  it("runs, snoozes and dismisses a workflow item through its own buttons", async () => {
    const workflowItem = item({ source: "workflow_run" });
    const { onAction } = renderWithAction(workflowItem, "basic");

    await userEvent.click(screen.getByRole("button", { name: "지금 실행" }));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith(workflowItem, "run_now", false));

    await userEvent.click(screen.getByRole("button", { name: "하루 일시 중지" }));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith(workflowItem, "snooze"));

    await userEvent.click(screen.getByRole("button", { name: "기각" }));
    await waitFor(() =>
      expect(onAction).toHaveBeenCalledWith(workflowItem, "dismiss", false, undefined),
    );
  });
});

describe("ReviewCard feedback", () => {
  it("shows an error with its raw backend detail demoted underneath", () => {
    renderFeedback(item(), { tone: "error", message: "실행하지 못했어요", detail: "HTTP 503" });
    expect(screen.getByText(/실행하지 못했어요/)).toBeTruthy();
    expect(screen.getByText("HTTP 503")).toBeTruthy();
  });

  it("does not repeat the message as its own detail", () => {
    renderFeedback(item(), { tone: "success", message: "실행됨 · run-1", detail: "실행됨 · run-1" });
    expect(screen.getAllByText(/실행됨 · run-1/)).toHaveLength(1);
  });

  it("shows a plain success with no detail", () => {
    const { container } = renderFeedback(item(), { tone: "success", message: "실행됨" });
    expect(container.querySelector(".review-card-feedback.is-ok")).toBeTruthy();
    expect(container.querySelector(".review-card-feedback.is-error")).toBeNull();
  });

  it("swaps in the rebase recovery when a proposal approval hit the 409 guard", () => {
    renderFeedback(PROPOSAL, { tone: "error", message: "파일이 그 사이 변경되었습니다", conflict: true });
    expect(screen.getByTestId("proposal-conflict-note")).toBeTruthy();
    expect(screen.getByRole("button", { name: /다시 읽어서 재적용/ })).toBeTruthy();
  });

  it("falls back to plain feedback when a conflict is reported for a non-proposal", () => {
    renderFeedback(item(), { tone: "error", message: "충돌", conflict: true });
    expect(screen.queryByTestId("proposal-conflict-note")).toBeNull();
    expect(screen.getByText(/충돌/)).toBeTruthy();
  });
});
