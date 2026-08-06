import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { rebaseProposal, unifiedDiffLines } from "./proposalRebase";

afterEach(() => {
  vi.unstubAllGlobals();
});

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

  it("handles a file emptied out entirely", () => {
    const diff = unifiedDiffLines("keep\ndrop", "", "gone.txt");
    expect(diff).toEqual(["--- a/gone.txt", "+++ b/gone.txt", "-keep", "-drop"]);
  });

  it("falls back to a coarse remove-all/add-all listing for very large files", () => {
    // The LCS table is O(n·m); past 400 lines a side it stops being worth it,
    // and the preview is capped either way.
    const before = Array.from({ length: 401 }, (_, i) => `old ${i}`).join("\n");
    const after = Array.from({ length: 401 }, (_, i) => `new ${i}`).join("\n");
    const diff = unifiedDiffLines(before, after, "big.txt");
    expect(diff).toHaveLength(400); // MAX_DIFF_LINES
    expect(diff[0]).toBe("--- a/big.txt");
    expect(diff[2]).toBe("-old 0");
    // Coarse means every "before" line is listed as removed, in order.
    expect(diff[3]).toBe("-old 1");
  });
});

function mockProposalDetail(payload: unknown, item: Record<string, unknown> = {}) {
  vi.spyOn(latticeApi, "proposalDetail").mockResolvedValue({
    ok: true, status: 200, source: "live",
    data: { id: "prop-1", title: "파일 수정 제안: docs/a.md", summary: "s", kind: "file_update", payload, ...item },
  } as never);
}

/** The three writes a rebase performs, stubbed and handed back for assertions. */
function mockRebaseWrites({
  current,
  created,
}: {
  current?: Record<string, unknown>;
  created?: Record<string, unknown>;
} = {}) {
  const read = vi.spyOn(latticeApi, "readWorkspaceFile").mockResolvedValue({
    ok: true, status: 200, source: "live", data: { content: "drifted body" }, ...current,
  } as never);
  const create = vi.spyOn(latticeApi, "createReviewItem").mockResolvedValue({
    ok: true, status: 200, source: "live", data: { id: "prop-2" }, ...created,
  } as never);
  const reject = vi.spyOn(latticeApi, "rejectProposal").mockResolvedValue({
    ok: true, status: 200, source: "live", data: {},
  } as never);
  return { read, create, reject };
}

const MISSING_FILE = { ok: false, status: 404, source: "unavailable", data: { content: "" } };

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

  it("reports the bare status when the read failed without a message", async () => {
    mockProposalDetail({ path: "docs/a.md", new_content: "x" });
    mockRebaseWrites({ current: { ok: false, status: 503, error: undefined } });
    await expect(rebaseProposal("prop-1")).rejects.toThrow("HTTP 503");
  });

  it("gives up when the staged proposal itself cannot be read", async () => {
    vi.spyOn(latticeApi, "proposalDetail").mockResolvedValue({
      ok: false, status: 404, source: "unavailable", data: {}, error: "proposal not found",
    } as never);
    await expect(rebaseProposal("prop-1")).rejects.toThrow("proposal not found");

    vi.spyOn(latticeApi, "proposalDetail").mockResolvedValue({
      ok: false, status: 500, source: "unavailable", data: {},
    } as never);
    await expect(rebaseProposal("prop-1")).rejects.toThrow("HTTP 500");
  });

  it("refuses a proposal that names no file", async () => {
    // A payload that is not even an object, and a kind the backend omitted:
    // both degrade rather than staging a proposal against nothing.
    mockProposalDetail("not-an-object", { kind: undefined });
    mockRebaseWrites();
    await expect(rebaseProposal("prop-1")).rejects.toThrow("proposal payload has no path");
  });

  it("stages against an empty base when the file is gone", async () => {
    mockProposalDetail({ path: "docs/a.md", new_content: "fresh body" });
    const { create, reject } = mockRebaseWrites({ current: MISSING_FILE });

    expect(await rebaseProposal("prop-1")).toBe("rebased");
    const payload = create.mock.calls[0][0].payload as Record<string, unknown>;
    // No base to hash, so the snapshot records "the file did not exist".
    expect(payload.base_exists).toBe(false);
    expect(payload.base_sha256).toBe("");
    expect(payload.before_bytes).toBe(0);
    expect(reject).toHaveBeenCalledWith("prop-1", "rebased_to:prop-2");
  });

  it("retires a delete proposal whose file is already gone", async () => {
    mockProposalDetail({ path: "docs/a.md" }, { kind: "file_delete" });
    const { create, reject } = mockRebaseWrites({ current: MISSING_FILE });

    expect(await rebaseProposal("prop-1")).toBe("already_applied");
    expect(create).not.toHaveBeenCalled();
    expect(reject).toHaveBeenCalledWith("prop-1", "rebase: file already deleted");
  });

  it("re-stages a delete proposal as a large-tier removal of the current file", async () => {
    mockProposalDetail({ path: "docs/a.md", new_content: "ignored" }, { kind: "file_delete" });
    const { create } = mockRebaseWrites();

    expect(await rebaseProposal("prop-1")).toBe("rebased");
    const body = create.mock.calls[0][0];
    expect(body.kind).toBe("file_delete");
    const payload = body.payload as Record<string, unknown>;
    // A delete carries no replacement content, and never counts as a small edit.
    expect(payload.new_content).toBeUndefined();
    expect(payload.tier).toBe("large");
    expect(payload.after_bytes).toBe(0);
  });

  it("marks a long rewrite as large tier", async () => {
    const long = Array.from({ length: 60 }, (_, i) => `line ${i}`).join("\n");
    mockProposalDetail({ path: "docs/a.md", new_content: long });
    const { create } = mockRebaseWrites();

    await rebaseProposal("prop-1");
    expect((create.mock.calls[0][0].payload as Record<string, unknown>).tier).toBe("large");
  });

  it("omits the base snapshot entirely when WebCrypto is unavailable", async () => {
    // Staging a wrong/empty hash would produce a proposal that can never be
    // approved, so the fields are left off (legacy apply-as-reviewed).
    vi.stubGlobal("crypto", {});
    mockProposalDetail({ path: "docs/a.md", new_content: "fresh" });
    const { create } = mockRebaseWrites();

    await rebaseProposal("prop-1");
    const payload = create.mock.calls[0][0].payload as Record<string, unknown>;
    expect("base_sha256" in payload).toBe(false);
    expect("base_exists" in payload).toBe(false);
  });

  it("names the fresh proposal after the file when the old one had no title", async () => {
    mockProposalDetail({ path: "docs/a.md", new_content: "fresh" }, { title: null, summary: 42 });
    const { create } = mockRebaseWrites();

    await rebaseProposal("prop-1");
    expect(create.mock.calls[0][0].title).toBe("docs/a.md");
    expect(create.mock.calls[0][0].summary).toBe("");
  });

  it("throws when the fresh proposal cannot be staged", async () => {
    mockProposalDetail({ path: "docs/a.md", new_content: "fresh" });
    const { create, reject } = mockRebaseWrites({
      created: { ok: false, status: 422, error: "invalid payload" },
    });
    await expect(rebaseProposal("prop-1")).rejects.toThrow("invalid payload");
    // The conflicted proposal is left alone; nothing replaced it.
    expect(reject).not.toHaveBeenCalled();

    create.mockResolvedValue({ ok: false, status: 500, source: "unavailable", data: {} } as never);
    await expect(rebaseProposal("prop-1")).rejects.toThrow("HTTP 500");
  });

  it("still retires the old proposal when the new one came back without an id", async () => {
    mockProposalDetail({ path: "docs/a.md", new_content: "fresh" });
    const { reject } = mockRebaseWrites({ created: { data: {} } });

    expect(await rebaseProposal("prop-1")).toBe("rebased");
    expect(reject).toHaveBeenCalledWith("prop-1", "rebased");
  });
});
