/**
 * The Review Center's vocabulary layer.
 *
 * Every label here has the same shape of contract: the **id** is what the
 * backend owns, the words are what this app owns, and an id this app has never
 * seen must still render as *something* — a new backend source or risk class
 * appearing in the queue is a normal event, not an error. These tests pin both
 * halves, plus the date formatting that can silently print "Invalid Date".
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewItem } from "@/api/client";
import {
  defaultSnoozeUntil,
  formatSnoozedUntil,
  hasRunBefore,
  isActionableReview,
  reviewChangeClassLabel,
  reviewRiskLabel,
  reviewSourceDetail,
  reviewSourceFilters,
  reviewSourceLabel,
  reviewStatusFilters,
  reviewStatusLabel,
  reviewStatusVariant,
} from "./reviewHelpers";

function item(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: "rev-1",
    status: "pending",
    effective_status: "pending",
    title: "제목",
    summary: "요약",
    source: "workflow_run",
    kind: "followup",
    payload: {},
    provenance: {},
    created_at: "2026-06-22T12:00:00Z",
    updated_at: "2026-06-22T12:00:00Z",
    ...overrides,
  } as ReviewItem;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("filter tables", () => {
  it("offer every status and source the API accepts", () => {
    expect(reviewStatusFilters.map((f) => f.id)).toEqual([
      "pending",
      "snoozed",
      "all",
      "approved",
      "dismissed",
    ]);
    expect(reviewSourceFilters[0].id).toBe("all");
    expect(reviewSourceFilters.map((f) => f.id)).toContain("change_proposal");
  });
});

describe("reviewStatusVariant", () => {
  it("maps each status onto its badge tone", () => {
    expect(reviewStatusVariant("pending")).toBe("warning");
    expect(reviewStatusVariant("snoozed")).toBe("muted");
    expect(reviewStatusVariant("approved")).toBe("success");
    expect(reviewStatusVariant("dismissed")).toBe("danger");
    expect(reviewStatusVariant("something_new")).toBe("muted");
  });
});

describe("reviewStatusLabel", () => {
  it("translates the statuses it knows", () => {
    expect(reviewStatusLabel("ko", "pending")).toBe("검토 대기");
    expect(reviewStatusLabel("en", "dismissed")).toBe("Dismissed");
  });

  it("shows an unknown status verbatim rather than an i18n key", () => {
    expect(reviewStatusLabel("ko", "escalated")).toBe("escalated");
  });
});

describe("reviewSourceLabel", () => {
  it("translates the sources it knows", () => {
    expect(reviewSourceLabel("ko", "change_proposal")).toBe("변경 제안");
    expect(reviewSourceLabel("en", "kg_change_digest")).toBe("Knowledge digest");
  });

  it("shows an unknown source verbatim", () => {
    expect(reviewSourceLabel("ko", "inbox_rule")).toBe("inbox_rule");
  });

  it("falls back to the generic word when there is no source at all", () => {
    expect(reviewSourceLabel("ko")).toBe("자동화");
    expect(reviewSourceLabel("en", "")).toBe("Automation");
  });
});

describe("reviewSourceDetail", () => {
  it("prefers the explicit source_detail", () => {
    expect(reviewSourceDetail("ko", { source_detail: "매일 09:00 브리핑" }, "trigger")).toBe(
      "매일 09:00 브리핑",
    );
  });

  it("falls through a blank source_detail to the trigger id", () => {
    expect(reviewSourceDetail("ko", { source_detail: "   ", trigger_id: "trg-7" }, "trigger")).toBe("trg-7");
  });

  it("falls back to the source label when the provenance is empty", () => {
    expect(reviewSourceDetail("ko", {}, "trigger")).toBe("트리거");
    expect(reviewSourceDetail("ko", { source_detail: null, trigger_id: "  " })).toBe("자동화");
  });
});

describe("reviewRiskLabel / reviewChangeClassLabel", () => {
  it("translate the governance classes they know", () => {
    expect(reviewRiskLabel("ko", "write_scoped")).toBe("작업 폴더 안에서만 수정");
    expect(reviewChangeClassLabel("en", "destructive")).toBe("Removes content");
  });

  it("show an unrecognised class verbatim", () => {
    expect(reviewRiskLabel("ko", "quantum")).toBe("quantum");
    expect(reviewChangeClassLabel("ko", "rewrite")).toBe("rewrite");
  });

  it("render nothing for a missing or non-string class", () => {
    expect(reviewRiskLabel("ko", undefined)).toBe("");
    expect(reviewRiskLabel("ko", 3)).toBe("");
    expect(reviewChangeClassLabel("ko", "   ")).toBe("");
    expect(reviewChangeClassLabel("ko", null)).toBe("");
  });
});

describe("defaultSnoozeUntil", () => {
  it("snoozes exactly one day out, as an ISO instant", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-22T12:00:00Z"));
    expect(defaultSnoozeUntil()).toBe("2026-06-23T12:00:00.000Z");
  });
});

describe("formatSnoozedUntil", () => {
  it("says only 'snoozed' when no deadline came back", () => {
    expect(formatSnoozedUntil("ko", null)).toBe("일시 중지됨");
    expect(formatSnoozedUntil("en")).toBe("Snoozed");
  });

  it("formats a real timestamp for the reader's locale", () => {
    const ko = formatSnoozedUntil("ko", "2026-06-23T12:00:00Z");
    const en = formatSnoozedUntil("en", "2026-06-23T12:00:00Z");
    expect(ko).toContain("까지 일시 중지");
    expect(ko).toContain("2026");
    expect(en).toContain("Snoozed until");
    expect(en).toContain("2026");
    expect(ko).not.toBe(en);
  });

  it("shows the raw value rather than 'Invalid Date' when it cannot be parsed", () => {
    expect(formatSnoozedUntil("en", "tomorrow-ish")).toBe("Snoozed until tomorrow-ish");
  });
});

describe("isActionableReview", () => {
  it("is true only while an item is still waiting", () => {
    expect(isActionableReview(item({ effective_status: "pending" }))).toBe(true);
    expect(isActionableReview(item({ effective_status: "snoozed" }))).toBe(true);
    expect(isActionableReview(item({ effective_status: "approved" }))).toBe(false);
  });
});

describe("hasRunBefore", () => {
  it("is true once a run id exists in either the payload or the provenance", () => {
    expect(hasRunBefore(item({ payload: { last_run_id: "run-1" } }))).toBe(true);
    expect(hasRunBefore(item({ provenance: { run_id: "run-2" } }))).toBe(true);
  });

  it("is false for an item that has never run, or that carries no metadata", () => {
    expect(hasRunBefore(item())).toBe(false);
    expect(hasRunBefore(item({ payload: undefined, provenance: undefined }))).toBe(false);
  });
});
