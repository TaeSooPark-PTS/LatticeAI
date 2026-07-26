import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { StaleEmbedderNotice } from "./BrainSignals";

function mockFreshness(status: string) {
  return vi.spyOn(latticeApi, "brainVectorFreshness").mockResolvedValue({
    ok: true, status: 200, source: "live",
    data: { status, pending_items: 0, total_items: 12, detail: "" },
  } as never);
}

function renderNotice() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StaleEmbedderNotice language="ko" />
    </QueryClientProvider>,
  );
}

describe("StaleEmbedderNotice", () => {
  it("stays silent while the index is healthy", async () => {
    mockFreshness("ready");
    renderNotice();
    await waitFor(() => expect(latticeApi.brainVectorFreshness).toHaveBeenCalled());
    expect(screen.queryByTestId("stale-embedder-notice")).toBeNull();
  });

  it("stays silent for ordinary pending indexing (that chip owns it)", async () => {
    mockFreshness("pending");
    renderNotice();
    await waitFor(() => expect(latticeApi.brainVectorFreshness).toHaveBeenCalled());
    expect(screen.queryByTestId("stale-embedder-notice")).toBeNull();
  });

  it("names the embedder swap and offers the one action that fixes it", async () => {
    mockFreshness("stale_embedder");
    const rebuild = vi.spyOn(latticeApi, "memoryRebuild").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { status: "ok" },
    } as never);
    renderNotice();
    await waitFor(() => expect(screen.getByTestId("stale-embedder-notice")).toBeTruthy());
    await userEvent.click(screen.getByTestId("stale-embedder-reindex"));
    expect(rebuild).toHaveBeenCalled();
  });

  it("reports a failed re-index instead of pretending it worked", async () => {
    mockFreshness("stale_embedder");
    vi.spyOn(latticeApi, "memoryRebuild").mockResolvedValue({
      ok: false, status: 500, source: "live", error: "boom", data: {},
    } as never);
    renderNotice();
    await waitFor(() => expect(screen.getByTestId("stale-embedder-notice")).toBeTruthy());
    await userEvent.click(screen.getByTestId("stale-embedder-reindex"));
    await waitFor(() =>
      expect(screen.getByText("다시 인덱싱에 실패했어요. 잠시 후 다시 시도해 주세요.")).toBeTruthy(),
    );
  });
});
