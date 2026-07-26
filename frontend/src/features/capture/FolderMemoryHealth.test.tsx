import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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
});
