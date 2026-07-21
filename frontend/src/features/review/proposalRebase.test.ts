import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { rebaseProposal, unifiedDiffLines } from "./proposalRebase";

describe("unifiedDiffLines", () => {
  it("emits headers plus -/+ lines for changed content", () => {
    const diff = unifiedDiffLines("a\nb\nc", "a\nB\nc", "notes.txt");
    expect(diff[0]).toBe("--- a/notes.txt");
    expect(diff[1]).toBe("+++ b/notes.txt");
    expect(diff).toContain("-b");
    expect(diff).toContain("+B");
    expect(diff).not.toContain("-a");
  });

  it("returns an empty diff for identical content", () => {
    expect(unifiedDiffLines("same\ntext", "same\ntext", "x.txt")).toEqual([]);
  });

  it("handles pure additions against a missing base", () => {
    const diff = unifiedDiffLines("", "hello\nworld", "new.txt");
    expect(diff).toContain("+hello");
    expect(diff).toContain("+world");
    // No removal lines beyond the "--- a/…" header.
    expect(diff.slice(2).some((line) => line.startsWith("-"))).toBe(false);
  });
});

function mockProposalDetail(payload: Record<string, unknown>) {
  vi.spyOn(latticeApi, "proposalDetail").mockResolvedValue({
    ok: true, status: 200, source: "live",
    data: { id: "prop-1", title: "파일 수정 제안: docs/a.md", summary: "s", kind: "file_update", payload },
  } as never);
}

describe("rebaseProposal", () => {
  it("re-reads the file, stages a fresh proposal, and retires the old one", async () => {
    mockProposalDetail({ path: "docs/a.md", new_content: "new body", diff: [] });
    vi.spyOn(latticeApi, "readWorkspaceFile").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { content: "drifted body" },
    } as never);
    const create = vi.spyOn(latticeApi, "createReviewItem").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { id: "prop-2" },
    } as never);
    const reject = vi.spyOn(latticeApi, "rejectProposal").mockResolvedValue({
      ok: true, status: 200, source: "live", data: {},
    } as never);

    const outcome = await rebaseProposal("prop-1");
    expect(outcome).toBe("rebased");
    expect(create).toHaveBeenCalledTimes(1);
    const body = create.mock.calls[0][0];
    expect(body.source).toBe("change_proposal");
    expect(body.kind).toBe("file_update");
    const payload = body.payload as Record<string, unknown>;
    expect(payload.path).toBe("docs/a.md");
    expect(payload.new_content).toBe("new body");
    expect((payload.diff as string[]).some((line) => line.startsWith("+new body"))).toBe(true);
    expect(reject).toHaveBeenCalledWith("prop-1", "rebased_to:prop-2");
  });

  it("retires the proposal without re-staging when the content already landed", async () => {
    mockProposalDetail({ path: "docs/a.md", new_content: "same body" });
    vi.spyOn(latticeApi, "readWorkspaceFile").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { content: "same body" },
    } as never);
    const create = vi.spyOn(latticeApi, "createReviewItem").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { id: "unused" },
    } as never);
    const reject = vi.spyOn(latticeApi, "rejectProposal").mockResolvedValue({
      ok: true, status: 200, source: "live", data: {},
    } as never);

    const outcome = await rebaseProposal("prop-1");
    expect(outcome).toBe("already_applied");
    expect(create).not.toHaveBeenCalled();
    expect(reject).toHaveBeenCalledTimes(1);
  });

  it("fails loudly when the current file cannot be read (non-404)", async () => {
    mockProposalDetail({ path: "docs/a.md", new_content: "x" });
    vi.spyOn(latticeApi, "readWorkspaceFile").mockResolvedValue({
      ok: false, status: 500, source: "unavailable", data: { content: "" }, error: "boom",
    } as never);
    await expect(rebaseProposal("prop-1")).rejects.toThrow("boom");
  });
});
