import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { fail, ok, renderPage } from "@/test/renderPage";
import { BrainIntelligencePanel } from "./BrainIntelligencePanel";

function renderPanel(api: Record<string, unknown> = {}) {
  return renderPage(<BrainIntelligencePanel language="ko" />, { api });
}

async function expand() {
  await userEvent.click(screen.getByRole("button", { name: /Brain 지능 진단/ }));
}

const fullHealth = {
  overall_score: 82,
  grade: "good",
  dimensions: {
    freshness: { score: 91 },
    connectivity: {}, // record without numeric score → em dash
    // embedding_coverage / consistency absent → em dash via the non-record side
  },
  recommended_actions: [
    { id: "rebuild_vector_index" },
    { id: "custom_check", reason: "커스텀 사유" },
    { id: "unknown_action" },
    "malformed-entry",
  ],
};

describe("BrainIntelligencePanel", () => {
  it("holds every diagnostic query until the panel is opened", async () => {
    renderPanel();
    expect(vi.mocked(latticeApi.brainHealth)).not.toHaveBeenCalled();
    expect(vi.mocked(latticeApi.brainInsights)).not.toHaveBeenCalled();
    expect(vi.mocked(latticeApi.brainContradictions)).not.toHaveBeenCalled();

    await expand();
    await waitFor(() => expect(vi.mocked(latticeApi.brainHealth)).toHaveBeenCalled());
    expect(vi.mocked(latticeApi.brainInsights)).toHaveBeenCalled();
    expect(vi.mocked(latticeApi.brainContradictions)).toHaveBeenCalled();
  });

  it("shows the loading note while the health check is in flight", async () => {
    let release!: (value: unknown) => void;
    renderPanel({ brainHealth: () => new Promise<unknown>((resolve) => { release = resolve; }) });
    await expand();
    expect(screen.getByText("진단 중...")).toBeTruthy();

    release(ok(fullHealth));
    expect(await screen.findByText("신선도")).toBeTruthy();
    expect(screen.queryByText("진단 중...")).toBeNull();
  });

  it("renders score, grade, dimensions, insights, contradictions and localized actions", async () => {
    const { container } = renderPanel({
      brainHealth: ok(fullHealth),
      brainInsights: ok({
        activity: { recent_nodes: 4 },
        attention: { stale_nodes: "2", orphan_nodes: "not-a-number" },
      }),
      brainContradictions: ok({ count: 3 }),
    });
    await expand();

    expect(await screen.findByText("종합 점수: 82")).toBeTruthy();
    expect(screen.getByText("좋음")).toBeTruthy();

    const dimensions = Array.from(container.querySelectorAll(".brain-intelligence-dimension"));
    expect(dimensions).toHaveLength(4);
    expect(dimensions[0].textContent).toContain("신선도");
    expect(dimensions[0].textContent).toContain("91");
    expect(dimensions[1].textContent).toContain("—"); // record without score
    expect(dimensions[2].textContent).toContain("—"); // dimension missing entirely

    expect(screen.getByText("최근 7일 새 지식 4개")).toBeTruthy();
    expect(screen.getByText("오래된 지식 2개")).toBeTruthy();
    expect(screen.getByText("연결 없는 지식 0개")).toBeTruthy(); // non-numeric → 0
    expect(screen.getByText("어긋나는 기억 3건")).toBeTruthy();

    // Known action ids localize; unknown ones fall back to reason, then id.
    expect(screen.getByText("의미 검색 색인 다시 만들기")).toBeTruthy();
    expect(screen.getByText("커스텀 사유")).toBeTruthy();
    expect(screen.getByText("unknown_action")).toBeTruthy();
  });

  it("hides the header score and action list when the payload is thin", async () => {
    renderPanel({
      brainHealth: ok({ grade: 42, dimensions: null, recommended_actions: "none" }),
      brainInsights: fail("insights down", {}),
      brainContradictions: ok(null),
    });
    await expand();

    await screen.findByText("신선도");
    expect(screen.queryByText(/종합 점수/)).toBeNull(); // overall_score missing
    expect(screen.queryByText("추천 관리 작업")).toBeNull(); // actions not an array
    expect(screen.getByText("최근 7일 새 지식 0개")).toBeTruthy(); // insights failed
    expect(screen.getByText("어긋나는 기억 0건")).toBeTruthy(); // non-record payload
  });

  it("labels unknown grades honestly", async () => {
    renderPanel({ brainHealth: ok({ overall_score: 10, grade: "weird", dimensions: {}, recommended_actions: [] }) });
    await expand();
    expect(await screen.findByText("종합 점수: 10")).toBeTruthy();
    expect(screen.getByText("데이터 없음")).toBeTruthy();
  });

  it("treats a failed health check as an error state and retries all three probes", async () => {
    const health = vi.fn()
      .mockResolvedValueOnce(fail("서버 다운", {}))
      .mockResolvedValue(ok(fullHealth));
    renderPanel({ brainHealth: health });
    await expand();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("지금은 브레인 상태를 불러올 수 없어요. 잠시 후 다시 시도해 주세요.");
    expect(alert.textContent).toContain("서버 다운");

    const insightCalls = vi.mocked(latticeApi.brainInsights).mock.calls.length;
    const contradictionCalls = vi.mocked(latticeApi.brainContradictions).mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(await screen.findByText("신선도")).toBeTruthy();
    expect(health).toHaveBeenCalledTimes(2);
    expect(vi.mocked(latticeApi.brainInsights).mock.calls.length).toBeGreaterThan(insightCalls);
    expect(vi.mocked(latticeApi.brainContradictions).mock.calls.length).toBeGreaterThan(contradictionCalls);
  });

  it("shows the error state without a detail line when the failure carries none", async () => {
    renderPanel({ brainHealth: { ok: false, status: 503, source: "unavailable", data: {} } });
    await expand();
    const alert = await screen.findByRole("alert");
    expect(alert.querySelector("small")).toBeNull();
  });

  it("treats a degraded live response and a thrown fetch as errors too", async () => {
    // ok:true but source "unavailable" — degraded data must not render as zeros.
    renderPanel({ brainHealth: { ok: true, status: 200, source: "unavailable", data: {} } });
    await expand();
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("shows the error state when the health request itself throws", async () => {
    renderPanel({ brainHealth: () => Promise.reject(new Error("network")) });
    await expand();
    const alert = await screen.findByRole("alert");
    expect(alert.querySelector("small")).toBeNull(); // no envelope → no detail
  });

  it("shows the error state when the client returns a malformed null envelope", async () => {
    renderPanel({ brainHealth: () => Promise.resolve(null) });
    await expand();
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  describe("consolidation", () => {
    it("previews duplicates, offers apply, and reports the pruned count after applying", async () => {
      const consolidate = vi.fn((apply: boolean) =>
        Promise.resolve(
          apply
            ? ok({ mode: "applied", pruned: 2 })
            : ok({ mode: "preview", duplicate_memory_count: 2, duplicate_edge_count: 1 }),
        ),
      );
      renderPanel({ brainHealth: ok(fullHealth), brainConsolidate: consolidate });
      await expand();
      await screen.findByText("신선도");
      const healthCalls = vi.mocked(latticeApi.brainHealth).mock.calls.length;

      await userEvent.click(screen.getByRole("button", { name: "중복 정리 미리보기" }));
      expect(await screen.findByText("중복 기억 2개 · 중복 연결 1개 발견")).toBeTruthy();

      await userEvent.click(screen.getByRole("button", { name: "중복 기억 정리 실행" }));
      expect(await screen.findByText("중복 기억 2개를 정리했습니다.")).toBeTruthy();
      expect(consolidate).toHaveBeenLastCalledWith(true);
      // Applying invalidates the health check and hides the apply button again.
      expect(screen.queryByRole("button", { name: "중복 기억 정리 실행" })).toBeNull();
      await waitFor(() =>
        expect(vi.mocked(latticeApi.brainHealth).mock.calls.length).toBeGreaterThan(healthCalls),
      );
    });

    it("reports edge-only duplicates without offering the memory prune", async () => {
      renderPanel({
        brainHealth: ok(fullHealth),
        brainConsolidate: ok({ mode: "preview", duplicate_memory_count: 0, duplicate_edge_count: 3 }),
      });
      await expand();
      await screen.findByText("신선도");
      await userEvent.click(screen.getByRole("button", { name: "중복 정리 미리보기" }));
      expect(await screen.findByText("중복 기억 0개 · 중복 연결 3개 발견")).toBeTruthy();
      expect(screen.queryByRole("button", { name: "중복 기억 정리 실행" })).toBeNull();
    });

    it("keeps the preview verdict when the apply fails", async () => {
      const consolidate = vi.fn((apply: boolean) =>
        Promise.resolve(
          apply
            ? fail("apply down", {})
            : ok({ mode: "preview", duplicate_memory_count: 2, duplicate_edge_count: 0 }),
        ),
      );
      renderPanel({ brainHealth: ok(fullHealth), brainConsolidate: consolidate });
      await expand();
      await screen.findByText("신선도");
      await userEvent.click(screen.getByRole("button", { name: "중복 정리 미리보기" }));
      await screen.findByText("중복 기억 2개 · 중복 연결 0개 발견");

      await userEvent.click(screen.getByRole("button", { name: "중복 기억 정리 실행" }));
      await waitFor(() => expect(consolidate).toHaveBeenLastCalledWith(true));
      // A failed apply must not overwrite the honest preview verdict.
      expect(screen.getByText("중복 기억 2개 · 중복 연결 0개 발견")).toBeTruthy();
    });

    it("says so when nothing is duplicated", async () => {
      renderPanel({
        brainHealth: ok(fullHealth),
        brainConsolidate: ok({ mode: "preview", duplicate_memory_count: 0, duplicate_edge_count: 0 }),
      });
      await expand();
      await screen.findByText("신선도");
      await userEvent.click(screen.getByRole("button", { name: "중복 정리 미리보기" }));
      expect(await screen.findByText("중복된 기억이 없습니다.")).toBeTruthy();
    });

    it("shows the working label while the preview runs and ignores failed or malformed results", async () => {
      let release!: (value: unknown) => void;
      const consolidate = vi.fn()
        .mockImplementationOnce(() => new Promise<unknown>((resolve) => { release = resolve; }))
        .mockResolvedValueOnce(ok(null));
      renderPanel({ brainHealth: ok(fullHealth), brainConsolidate: consolidate });
      await expand();
      await screen.findByText("신선도");

      await userEvent.click(screen.getByRole("button", { name: "중복 정리 미리보기" }));
      expect(await screen.findByRole("button", { name: "확인 중..." })).toBeDisabled();
      release(fail("consolidate down", {}));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "중복 정리 미리보기" })).toBeEnabled(),
      );
      // Failed envelope → no note rendered at all.
      expect(screen.queryByText(/중복/)).not.toBeNull(); // the preview button itself
      expect(screen.queryByText("중복된 기억이 없습니다.")).toBeNull();

      // ok but non-record data is ignored the same way.
      await userEvent.click(screen.getByRole("button", { name: "중복 정리 미리보기" }));
      await waitFor(() => expect(consolidate).toHaveBeenCalledTimes(2));
      expect(screen.queryByText("중복된 기억이 없습니다.")).toBeNull();
    });
  });
});
