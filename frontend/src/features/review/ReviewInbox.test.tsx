import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ok, renderPage } from "@/test/renderPage";
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

const REVIEWS = ok({
  items: [
    {
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
    },
  ],
});

function render() {
  return renderPage(<ReviewInbox />, {
    mode: "basic",
    api: { automationReviews: REVIEWS, proposalCounts: ok({ pending: 0 }) },
  });
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
});
