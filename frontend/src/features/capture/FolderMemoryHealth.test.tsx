import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { FolderMemoryHealthCard, parseFolderHealth } from "./FolderMemoryHealth";

const PAYLOAD = {
  count: 2,
  folders: [
    {
      id: "src-1",
      label: "Docs",
      status: "active",
      watch_active: true,
      files: { total: 10, indexed: 8, failed: 2, skipped: 0, pending: 0 },
      coverage: 0.8,
      recent_errors: [{ path: "bad.md", detail: "parser exploded" }],
    },
    {
      id: "src-2",
      label: "Empty",
      status: "active",
      watch_active: false,
      files: { total: 0, indexed: 0, failed: 0, skipped: 0, pending: 0 },
      coverage: null,
      recent_errors: [],
    },
  ],
  vector_freshness_global: { status: "pending", pending_items: 4, total_items: 40 },
};

function mockHealth(payload: unknown, ok = true) {
  return vi.spyOn(latticeApi, "localFolderHealth").mockResolvedValue({
    ok, status: ok ? 200 : 500, source: "live", data: payload,
  } as never);
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FolderMemoryHealthCard language="ko" />
    </QueryClientProvider>,
  );
}

describe("parseFolderHealth", () => {
  it("keeps unknown coverage as null so it is never shown as 0%", () => {
    const parsed = parseFolderHealth(PAYLOAD);
    expect(parsed.folders[0].coverage).toBe(0.8);
    expect(parsed.folders[1].coverage).toBeNull();
  });

  it("drops folders without an id and errors without a reason", () => {
    const parsed = parseFolderHealth({
      folders: [
        { id: "", label: "no id" },
        { id: "ok", label: "Ok", recent_errors: [{ path: "a" }, { path: "b", detail: "why" }] },
      ],
    });
    expect(parsed.folders.map((f) => f.id)).toEqual(["ok"]);
    expect(parsed.folders[0].errors).toEqual([{ path: "b", detail: "why" }]);
  });

  it("reads the vector figure as one global number", () => {
    const parsed = parseFolderHealth(PAYLOAD);
    expect(parsed.vectorStatus).toBe("pending");
    expect(parsed.vectorPending).toBe(4);
    expect(parseFolderHealth(null).vectorStatus).toBe("unavailable");
  });

  it("tolerates folder and error entries that are not records", () => {
    const parsed = parseFolderHealth({
      folders: [42, { id: "f1", recent_errors: ["oops", { path: "p", detail: "why" }] }],
    });
    expect(parsed.folders.map((f) => f.id)).toEqual(["f1"]);
    // No label or root_path → the id names the folder rather than a blank.
    expect(parsed.folders[0].label).toBe("f1");
    expect(parsed.folders[0].errors).toEqual([{ path: "p", detail: "why" }]);
  });
});

describe("FolderMemoryHealthCard", () => {
  it("shows coverage, failures with their reason, and the global vector note", async () => {
    mockHealth(PAYLOAD);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-memory-health")).toBeTruthy());
    expect(screen.getByText("80% 기억됨 (8/10)")).toBeTruthy();
    expect(screen.getByText("들어오지 못한 파일 2개")).toBeTruthy();
    expect(screen.getByText("parser exploded")).toBeTruthy();
    expect(screen.getByText(/폴더별 아님/)).toBeTruthy();
  });

  it("says a folder was not scanned instead of claiming 0%", async () => {
    mockHealth(PAYLOAD);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-health-src-2")).toBeTruthy());
    expect(screen.getByText("아직 살펴보지 않았어요")).toBeTruthy();
  });

  it("renders nothing when no folder is connected", async () => {
    mockHealth({ folders: [], count: 0 });
    renderCard();
    await waitFor(() => expect(latticeApi.localFolderHealth).toHaveBeenCalled());
    expect(screen.queryByTestId("folder-memory-health")).toBeNull();
  });

  it("omits the global vector note when nothing is pending", async () => {
    mockHealth({
      folders: [{ id: "solo", label: "Solo", coverage: 0.5, files: { total: 2, indexed: 1 } }],
    });
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-health-solo")).toBeTruthy());
    expect(screen.queryByText(/폴더별 아님/)).toBeNull();
  });

  it("reads deleted files from files.deleted or the deleted list", () => {
    const fromCount = parseFolderHealth({
      folders: [{ id: "a", root_path: "/tmp/a", files: { deleted: 3 } }],
    });
    expect(fromCount.folders[0].deleted).toBe(3);
    expect(fromCount.folders[0].rootPath).toBe("/tmp/a");
    const fromList = parseFolderHealth({
      folders: [{ id: "b", root_path: "/tmp/b", deleted: ["gone.md", "also.md"] }],
    });
    expect(fromList.folders[0].deleted).toBe(2);
  });
});

describe("FolderMemoryHealthCard prune", () => {
  it("hides the cleanup action when nothing was deleted", async () => {
    mockHealth(PAYLOAD);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-memory-health")).toBeTruthy());
    expect(screen.queryByTestId("folder-prune-src-1")).toBeNull();
  });

  it("shows the cleanup action and confirms after a dry-run preview", async () => {
    mockHealth({
      folders: [{
        id: "src-1",
        label: "Docs",
        root_path: "/tmp/docs",
        files: { total: 10, indexed: 8, failed: 0, deleted: 2 },
        deleted: ["/tmp/docs/gone.md", "/tmp/docs/old.md"],
        coverage: 0.8,
      }],
    });
    const prune = vi.spyOn(latticeApi, "pruneFolderDeleted")
      .mockResolvedValueOnce({
        ok: true, status: 200, source: "live",
        data: { dry_run: true, files: ["a", "b"], would_remove: { nodes: 4, edges: 6, chunks: 3, vectors: 3 } },
      } as never)
      .mockResolvedValueOnce({
        ok: true, status: 200, source: "live",
        data: { dry_run: false, files: ["a", "b"], removed: { nodes: 4 } },
      } as never);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-prune-open-src-1")).toBeTruthy());
    expect(screen.getByText("삭제된 파일 정리 (2)")).toBeTruthy();
    await userEvent.click(screen.getByTestId("folder-prune-open-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-preview-src-1")).toBeTruthy());
    expect(prune).toHaveBeenCalledWith("/tmp/docs", false);
    expect(screen.getByText("노드 4개")).toBeTruthy();
    expect(screen.getByText("연결 6개")).toBeTruthy();
    await userEvent.click(screen.getByTestId("folder-prune-confirm-src-1"));
    await waitFor(() => expect(prune).toHaveBeenCalledWith("/tmp/docs", true));
  });

  it("renders a zero preview when the dry-run body is not a record", async () => {
    mockHealth({
      folders: [{
        id: "src-1",
        label: "Docs",
        root_path: "/tmp/docs",
        files: { deleted: 1 },
        deleted: ["gone.md"],
        coverage: 1,
      }],
    });
    vi.spyOn(latticeApi, "pruneFolderDeleted").mockResolvedValue({
      ok: true, status: 200, source: "live", data: 7,
    } as never);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-prune-open-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-open-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-preview-src-1")).toBeTruthy());
    expect(screen.getByText("노드 0개")).toBeTruthy();
  });

  it("falls back to the status when a failed preview has no error text", async () => {
    mockHealth({
      folders: [{
        id: "src-1",
        label: "Docs",
        root_path: "/tmp/docs",
        files: { deleted: 1 },
        deleted: ["gone.md"],
        coverage: 1,
      }],
    });
    vi.spyOn(latticeApi, "pruneFolderDeleted").mockResolvedValue({
      ok: false, status: 503, source: "unavailable", data: {},
    } as never);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-prune-open-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-open-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-error")).toBeTruthy());
    expect(screen.getByText(/503/)).toBeTruthy();
  });

  it("falls back to the status when a failed confirm has no error text", async () => {
    mockHealth({
      folders: [{
        id: "src-1",
        label: "Docs",
        root_path: "/tmp/docs",
        files: { deleted: 1 },
        deleted: ["gone.md"],
        coverage: 1,
      }],
    });
    vi.spyOn(latticeApi, "pruneFolderDeleted")
      .mockResolvedValueOnce({
        ok: true, status: 200, source: "live",
        data: { files: [], would_remove: "nope" },
      } as never)
      .mockResolvedValueOnce({
        ok: false, status: 409, source: "live", data: {},
      } as never);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-prune-open-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-open-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-confirm-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-confirm-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-error")).toBeTruthy());
    expect(screen.getByText(/409/)).toBeTruthy();
  });

  it("shows an error when the preview request fails", async () => {
    mockHealth({
      folders: [{
        id: "src-1",
        label: "Docs",
        root_path: "/tmp/docs",
        files: { deleted: 1 },
        deleted: ["gone.md"],
        coverage: 1,
      }],
    });
    vi.spyOn(latticeApi, "pruneFolderDeleted").mockResolvedValue({
      ok: false, status: 500, source: "unavailable", error: "down", data: {},
    } as never);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-prune-open-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-open-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-error")).toBeTruthy());
    expect(screen.getByText(/down/)).toBeTruthy();
  });

  it("shows an error when the confirm request fails", async () => {
    mockHealth({
      folders: [{
        id: "src-1",
        label: "Docs",
        root_path: "/tmp/docs",
        files: { deleted: 1 },
        deleted: ["gone.md"],
        coverage: 1,
      }],
    });
    vi.spyOn(latticeApi, "pruneFolderDeleted")
      .mockResolvedValueOnce({
        ok: true, status: 200, source: "live",
        data: { dry_run: true, files: ["gone.md"], would_remove: { nodes: 1, edges: 0, chunks: 0, vectors: 0 } },
      } as never)
      .mockResolvedValueOnce({
        ok: false, status: 500, source: "unavailable", error: "boom", data: {},
      } as never);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-prune-open-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-open-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-confirm-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-confirm-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-error")).toBeTruthy());
    expect(screen.getByText(/boom/)).toBeTruthy();
  });

  it("shows an error when the confirm call throws", async () => {
    mockHealth({
      folders: [{
        id: "src-1",
        label: "Docs",
        root_path: "/tmp/docs",
        files: { deleted: 1 },
        deleted: ["gone.md"],
        coverage: 1,
      }],
    });
    vi.spyOn(latticeApi, "pruneFolderDeleted")
      .mockResolvedValueOnce({
        ok: true, status: 200, source: "live",
        data: { dry_run: true, files: ["gone.md"], would_remove: { nodes: 1, edges: 0, chunks: 0, vectors: 0 } },
      } as never)
      .mockRejectedValueOnce(new Error("timeout"));
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-prune-open-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-open-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-confirm-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-confirm-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-error")).toBeTruthy());
    expect(screen.getByText(/timeout/)).toBeTruthy();
  });

  it("shows an error when the preview call throws", async () => {
    mockHealth({
      folders: [{
        id: "src-1",
        label: "Docs",
        root_path: "/tmp/docs",
        files: { deleted: 1 },
        deleted: ["gone.md"],
        coverage: 1,
      }],
    });
    vi.spyOn(latticeApi, "pruneFolderDeleted").mockRejectedValue(new Error("network"));
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-prune-open-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-open-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-error")).toBeTruthy());
    expect(screen.getByText(/network/)).toBeTruthy();
  });

  it("cancels the preview without calling the live prune", async () => {
    mockHealth({
      folders: [{
        id: "src-1",
        label: "Docs",
        root_path: "/tmp/docs",
        files: { deleted: 1 },
        deleted: ["gone.md"],
        coverage: 1,
      }],
    });
    const prune = vi.spyOn(latticeApi, "pruneFolderDeleted").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { dry_run: true, files: ["gone.md"], would_remove: { nodes: 1, edges: 1, chunks: 0, vectors: 0 } },
    } as never);
    renderCard();
    await waitFor(() => expect(screen.getByTestId("folder-prune-open-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-open-src-1"));
    await waitFor(() => expect(screen.getByTestId("folder-prune-cancel-src-1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("folder-prune-cancel-src-1"));
    expect(prune).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("folder-prune-preview-src-1")).toBeNull();
  });
});
