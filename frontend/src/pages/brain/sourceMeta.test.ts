import { describe, expect, it } from "vitest";

import { graphTypeLabel, groupLabelFor, sourceCreatedAt, sourceType } from "./sourceMeta";

describe("sourceType", () => {
  it("classifies known source kinds and falls back for the rest", () => {
    expect(sourceType({ source_type: "chat" }, "ko")).toBeTruthy();
    expect(sourceType({ type: "pdf" }, "ko")).toBeTruthy();
    expect(sourceType({ kind: "archive" }, "ko")).toBeTruthy();
    expect(sourceType({ metadata: { role: "note" } }, "ko")).toBeTruthy();
    expect(sourceType({}, "ko")).toBeTruthy();
  });
});

describe("sourceCreatedAt", () => {
  it("reads created_at, then metadata, then nothing", () => {
    expect(sourceCreatedAt({ created_at: "2026-01-01" })).toBe("2026-01-01");
    expect(sourceCreatedAt({ metadata: { timestamp: 9 } })).toBe("9");
    expect(sourceCreatedAt({})).toBe("");
  });
});

describe("graphTypeLabel", () => {
  it("uses entity copy when it exists and titleizes otherwise", () => {
    expect(graphTypeLabel("Document", "ko")).not.toBe("Document");
    expect(graphTypeLabel("MysteryType", "en")).toBe("MysteryType");
  });
});

describe("groupLabelFor", () => {
  it("returns the group's label when the id matches", () => {
    const node = { id: "n1", group: "knowledge", type: "Document", label: "n", importance: 1, degree: 0, source: "", summary: "", searchText: "n", raw: {} };
    expect(groupLabelFor(node as never, [{ id: "knowledge", label: "지식", color: "", count: 1, visibleCount: 1, collapsed: false }])).toBe("지식");
  });
});
