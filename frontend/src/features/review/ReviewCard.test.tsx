import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ApiResult, ReviewItem } from "@/api/client";
import { ok, renderPage } from "@/test/renderPage";
import { ReviewCard } from "./ReviewCard";

/**
 * The review card is where a person decides. 10.6.x split it into evidence on
 * the left and the approve/reject decision on the right, which introduced three
 * ways to get it wrong that a screenshot would not catch:
 *
 *   - the split rendering as a 7-column void when the item carries no evidence,
 *   - `<main>` emitted once per card in a list of cards,
 *   - visible copy sourced from a key that does not exist, so `t()` returns the
 *     key itself and the raw `review.status.title` shows up on screen.
 *
 * Each is asserted below.
 */

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

describe("ReviewCard", () => {
  it("never emits a <main> landmark, because a page renders many cards", () => {
    // A list of N cards must not produce N main landmarks.
    render(PROPOSAL, "advanced");
    expect(document.querySelectorAll("main").length).toBe(0);
  });

  it("scopes its header bar so it is not a page banner", () => {
    // Same failure mode as <main>, one element over. Per HTML-AAM a <header>
    // maps to `banner` unless it sits inside article/aside/main/nav/section —
    // so while this card's root was a plain <div>, an inbox of N cards
    // announced N page banners.
    //
    // Asserted structurally, not with getByRole("banner"): aria-query does
    // carry the constraint ("scoped to a sectioning content element" ->
    // generic), but @testing-library/dom does not evaluate constraints and
    // reports `banner` either way. The ancestor check is the rule itself.
    const { container } = render(PROPOSAL, "advanced");
    const headers = container.querySelectorAll("header, footer");
    expect(headers.length).toBeGreaterThan(0);
    for (const el of headers) {
      expect(el.closest("article, aside, main, nav, section")).not.toBeNull();
    }
  });

  it("makes the item title the card's heading, not the word 결정하기", () => {
    // Heading navigation is how a screen-reader user moves between review
    // items. When only the decision panel carried a heading, that list read as
    // N identical "결정하기" and never named a single item.
    render(PROPOSAL, "advanced");
    const titleHeading = screen.getByRole("heading", { name: "테스트 검토 항목" });
    const decisionHeading = screen.getByRole("heading", { name: "결정하기" });
    // The item outranks its own decision panel, and does not skip a level from
    // the inbox's <h2>.
    expect(titleHeading.tagName).toBe("H3");
    expect(decisionHeading.tagName).toBe("H4");
  });

  it("names the card by its title so each item is one navigable unit", () => {
    render(PROPOSAL, "advanced");
    expect(screen.getByRole("article", { name: "테스트 검토 항목" })).toBeTruthy();
  });

  it("shows the decision heading and filter copy as text, not as i18n keys", () => {
    render(PROPOSAL, "advanced");
    // `t()` falls back to the key string, so a missing key is silently rendered.
    expect(document.body.textContent).not.toMatch(/review\.[a-z]+\.[a-zA-Z]+/);
    expect(screen.getByRole("heading", { name: "결정하기" })).toBeTruthy();
  });

  it("names the approve/reject cluster as a group so the label is announced", () => {
    // A bare <div aria-label> is not exposed to assistive tech; it needs a role.
    render(PROPOSAL, "advanced");
    expect(screen.getByRole("group", { name: "검토 작업" })).toBeTruthy();
  });

  it("keeps both decisions reachable, and the reject reason with them", () => {
    render(PROPOSAL, "advanced");
    expect(screen.getByRole("button", { name: /승인/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /거절|기각/ })).toBeTruthy();
    // Rejecting a proposal records a reason; the field must survive the split.
    expect(screen.getByLabelText(/사유/)).toBeTruthy();
  });

  it("splits into two columns only when there is evidence to show", () => {
    const { container } = render(PROPOSAL, "advanced");
    expect(container.querySelector(".md\\:grid-cols-12")).toBeTruthy();
  });

  it("stays single-column when the item carries no evidence at all", () => {
    // A chat follow-up in basic mode has no diff, no risk badges and no
    // technical panel. Splitting it 7/5 leaves the evidence side empty.
    const { container } = render(item(), "basic");
    expect(container.querySelector(".md\\:grid-cols-12")).toBeNull();
    expect(container.querySelector(".md\\:col-span-5")).toBeNull();
    // The decision itself is still there.
    expect(screen.getByRole("group", { name: "검토 작업" })).toBeTruthy();
  });

  it("renders the diff for a proposal", () => {
    render(PROPOSAL, "advanced");
    expect(document.body.textContent).toContain("README.md");
    expect(document.body.textContent).toContain("새 줄");
  });
});
