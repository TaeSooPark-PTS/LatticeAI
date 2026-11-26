import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { latticeApi } from "@/api/client";
import { BrainVitalPill, extractGraphCounts } from "./BrainVitalPill";

function renderPill(language: "ko" | "en") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BrainVitalPill language={language} />
    </QueryClientProvider>,
  );
}

describe("BrainVitalPill", () => {
  it("extracts counts from explicit totals or nested type maps", () => {
    expect(extractGraphCounts({ total_nodes: 42, total_edges: 88 })).toEqual({ nodes: 42, edges: 88 });
    expect(extractGraphCounts({ nodes: { Topic: 3, File: 9 }, edges: { mentions: 6, based_on: 4 } })).toEqual({
      nodes: 12,
      edges: 10,
    });
    expect(extractGraphCounts(null)).toEqual({ nodes: 0, edges: 0 });
  });

  it("renders growth stage and live counts linked to the knowledge graph", async () => {
    vi.spyOn(latticeApi, "graphStats").mockResolvedValue({
      ok: true,
      status: 200,
      source: "api",
      data: { total_nodes: 96, total_edges: 140 },
    } as never);

    renderPill("en");
    const pill = await screen.findByTestId("brain-vital-pill");
    await vi.waitFor(() => {
      expect(pill.getAttribute("data-stage")).toBe("neural_web");
    });
    expect(pill.textContent).toContain("Neural Web");
    expect(pill.textContent).toContain("96");
    expect(pill.getAttribute("href")).toBe("#/knowledge-graph");
  });
});

describe("extractGraphCounts with irregular payloads", () => {
  it("accepts plain numbers and skips entries that are not positive counts", () => {
    expect(extractGraphCounts({ nodes: 7.4, edges: -2 })).toEqual({ nodes: 7, edges: 0 });
    expect(extractGraphCounts({ total_nodes: -1, nodes: { Topic: 2, Bad: "x", Gone: -4 }, edges: "many" })).toEqual({
      nodes: 2,
      edges: 0,
    });
  });
});
