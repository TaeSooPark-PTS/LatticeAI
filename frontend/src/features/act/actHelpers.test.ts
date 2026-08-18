import { describe, expect, it } from "vitest";

import { approvalActionLabel, firstString, humanRunTitle, runStatusLabel } from "./actHelpers";

describe("firstString", () => {
  it("returns the first non-empty string", () => {
    expect(firstString(null, "  ", "keep", "later")).toBe("keep");
    expect(firstString(1, false, undefined)).toBe("");
  });
});

describe("runStatusLabel", () => {
  it("translates a known status and falls back for an unknown one", () => {
    expect(runStatusLabel("unknown-status-xyz", "ko")).toBe("알 수 없음");
  });
});

describe("humanRunTitle", () => {
  it("prefers a named field, then a string input, then a nested object", () => {
    expect(humanRunTitle({ name: "직접" })).toBe("직접");
    expect(humanRunTitle({ input: "  질문  " })).toBe("질문");
    expect(humanRunTitle({ input: { prompt: "안에서" } })).toBe("안에서");
    expect(humanRunTitle({ input: { other: true } })).toBe("");
    expect(humanRunTitle({})).toBe("");
  });
});

describe("approvalActionLabel", () => {
  it("looks up the action token, then the server label, then the default", () => {
    expect(approvalActionLabel({ action: "file_read" }, "ko")).toBeTruthy();
    expect(approvalActionLabel({ action_label: "파일 읽기" }, "ko")).toBe("파일 읽기");
    expect(approvalActionLabel({}, "ko")).toBeTruthy();
  });
});
