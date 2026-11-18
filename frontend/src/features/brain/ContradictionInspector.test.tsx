import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n/brain";
import { BrainIntelligencePanel } from "./BrainIntelligencePanel";
import { ContradictionInspector } from "./ContradictionInspector";
import { useAppStore } from "@/store/appStore";
import { fail, ok, renderPage } from "@/test/renderPage";

describe("ContradictionInspector", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.location.hash = "";
    useAppStore.setState({ focusedGraphNodeId: null });
  });

  it("renders inside BrainIntelligencePanel when contradictions exist and jumps to Knowledge Graph", async () => {
    renderPage(<BrainIntelligencePanel language="ko" />, {
      api: {
        brainHealth: ok({ score: 78, grade: "good", dimensions: {}, recommended_actions: [] }),
        brainInsights: ok({ activity: { recent_nodes: 2 }, attention: { stale_nodes: 0, orphan_nodes: 0 } }),
        brainContradictions: ok({
          count: 3,
          items: [
            { left_id: "node-a", left_content: "포트 8000 사용", right_id: "node-b", right_content: "포트 8080 사용" },
            { source: "src-1", left_title: "구버전 정책", target: "tgt-1", right_title: "신버전 정책" },
            { id: "solo-1", content: "시점 충돌 메모" },
            {},
          ],
        }),
      },
    });

    await userEvent.click(screen.getByRole("button", { name: /Brain 지능 진단/ }));
    expect(await screen.findByTestId("contradiction-inspector")).toBeTruthy();
    expect(screen.getByText("포트 8000 사용 ↔ 포트 8080 사용")).toBeTruthy();
    expect(screen.getByText("구버전 정책 ↔ 신버전 정책")).toBeTruthy();
    expect(screen.getByText("시점 충돌 메모")).toBeTruthy();

    await userEvent.click(screen.getByTestId("contradiction-open-graph-node-a"));
    expect(useAppStore.getState().focusedGraphNodeId).toBe("node-a");
    expect(window.location.hash).toBe("#/knowledge-graph");
  });

  it(" covers fallback summary branches for target-only or id-only conflict items", () => {
    renderPage(
      <ContradictionInspector
        language="en"
        items={[
          { left_id: "l1", target: "t1" },
          { left_id: "l2", right_id: "r2" },
          { id: "only-id" },
        ]}
      />,
    );
    expect(screen.getByText("l1 ↔ t1")).toBeTruthy();
    expect(screen.getByText("l2 ↔ r2")).toBeTruthy();
    expect(screen.getByText("only-id")).toBeTruthy();
  });

  it("stages contradiction proposals and resolves them across keep_old, replace, and keep_both_temporal", async () => {
    let releasePropose: (val: unknown) => void = () => {};
    const propose = vi.fn().mockImplementation(
      () => new Promise((resolve) => { releasePropose = resolve; }),
    );
    const resolve = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, source: "live", data: {} })
      .mockResolvedValueOnce({ ok: true, status: 200, source: "live", data: {} })
      .mockResolvedValueOnce({ ok: true, status: 200, source: "live", data: {} })
      .mockResolvedValueOnce({ ok: false, status: 500, source: "unavailable", data: {} });

    renderPage(
      <ContradictionInspector
        language="en"
        items={[{ left_id: "n1", left_content: "A", right_id: "n2", right_content: "B" }]}
      />,
      {
        api: {
          brainProposeContradictions: propose,
          brainResolveContradiction: resolve,
        },
      },
    );

    await userEvent.click(screen.getByTestId("contradiction-stage-btn"));
    expect(await screen.findByText("Staging…")).toBeTruthy();
    releasePropose({
      ok: true,
      status: 200,
      source: "live",
      data: {
        proposed_count: 3,
        proposed: [
          { id: "rev-1", title: "Conflict 1", summary: "Summary 1" },
          { id: "rev-2", title: "", summary: "" },
          { id: "rev-3", title: "Conflict 3" },
          { title: "Missing ID ignored" },
        ],
      },
    });
    expect(await screen.findByTestId("staged-contradiction-rev-1")).toBeTruthy();
    expect(screen.getByText("Summary 1")).toBeTruthy();
    expect(screen.getByText("rev-2")).toBeTruthy();

    await userEvent.click(screen.getByTestId("resolve-contradiction-rev-1-keep_old"));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith("rev-1", "keep_old"));
    expect(screen.getByRole("status").textContent).toContain("Keep earlier");

    await userEvent.click(screen.getByTestId("resolve-contradiction-rev-2-replace"));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith("rev-2", "replace"));
    expect(screen.getByRole("status").textContent).toContain("Replace with newer");

    await userEvent.click(screen.getByTestId("resolve-contradiction-rev-3-keep_both_temporal"));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith("rev-3", "keep_both_temporal"));
    expect(screen.getByRole("status").textContent).toContain("Keep both (temporal)");
  });

  it("handles failed stage or failed resolution responses gracefully", async () => {
    const resolveFail = vi.fn().mockResolvedValue({ ok: false, status: 500, source: "unavailable", data: {} });
    const first = renderPage(
      <ContradictionInspector language="ko" items={[{ id: "n1", content: "충돌" }]} />,
      { api: { brainProposeContradictions: fail("boom", 500) } },
    );
    await userEvent.click(screen.getByTestId("contradiction-stage-btn"));
    expect(screen.queryByTestId(/staged-contradiction-/)).toBeNull();
    first.unmount();

    renderPage(
      <ContradictionInspector language="ko" items={[{ id: "n1", content: "충돌" }]} />,
      {
        api: {
          brainProposeContradictions: ok({ proposed: [{ id: "rev-x", title: "X" }] }),
          brainResolveContradiction: resolveFail,
        },
      },
    );
    await userEvent.click(screen.getByTestId("contradiction-stage-btn"));
    await screen.findByTestId("staged-contradiction-rev-x");
    await userEvent.click(screen.getByTestId("resolve-contradiction-rev-x-replace"));
    await waitFor(() => expect(resolveFail).toHaveBeenCalled());
    // Failed resolution keeps the staged card on screen so the user can retry.
    expect(screen.getByTestId("staged-contradiction-rev-x")).toBeTruthy();
  });
});
