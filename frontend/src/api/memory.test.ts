import { beforeEach, describe, expect, it, vi } from "vitest";

import { del, get, post } from "./base";
import { memoryApi } from "./memory";

vi.mock("./base", () => ({ del: vi.fn(), get: vi.fn(), post: vi.fn() }));

describe("memoryApi", () => {
  beforeEach(() => vi.clearAllMocks());

  it("calls the workspace memory routes with the handler shapes", () => {
    memoryApi.workspaceMemories();
    memoryApi.searchWorkspaceMemories("tea");
    memoryApi.addWorkspaceMemory("preferences", "green tea");
    memoryApi.deleteWorkspaceMemory("memory/1");

    expect(get).toHaveBeenNthCalledWith(1, "/workspace/memories", { memories: [] });
    expect(get).toHaveBeenNthCalledWith(2, "/workspace/memories/search", { memories: [] }, { q: "tea", limit: 100 });
    expect(post).toHaveBeenCalledWith("/workspace/memories", { kind: "preferences", content: "green tea" }, {});
    expect(del).toHaveBeenCalledWith("/workspace/memories/memory%2F1", {});
  });
});
