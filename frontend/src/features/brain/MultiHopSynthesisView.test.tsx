import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import "@/i18n/brain";
import { MultiHopSynthesisView } from "./MultiHopSynthesisView";

describe("MultiHopSynthesisView", () => {
  it("renders empty synthesis state when no reasoning paths or synthesis object are present", () => {
    render(<MultiHopSynthesisView language="ko" data={{}} />);
    expect(screen.getByTestId("multihop-synthesis-panel")).toBeTruthy();
    expect(screen.getByText(/직접 일치 0개 · 확장 발견 0개 · 추론 경로 0개/)).toBeTruthy();
    expect(screen.getByText(/추가 다단계 연결 경로가 발견되지 않았습니다/)).toBeTruthy();
  });

  it("renders bridge concepts, conflicts, and 1-hop / 2-hop reasoning chains with relation fallbacks", () => {
    render(
      <MultiHopSynthesisView
        language="en"
        data={{
          synthesis: {
            direct_hits: 2,
            multi_hop_hits: 3,
            bridge_concepts: [
              { id: "b1", title: "WAL Contention", node_type: "Concept", bridged_targets: 2 },
            ],
            contradictions: [
              {
                source_id: "s1",
                source_title: "Async Checkpointer RFC",
                target_id: "c1",
                target_title: "Disable Auto-Checkpoint",
                relation: "CONTRADICTS",
              },
            ],
          },
          reasoning_paths: [
            {
              seed_id: "s1",
              seed_title: "Async Checkpointer RFC",
              bridge_id: "b1",
              bridge_title: "WAL Contention",
              target_id: "t2",
              target_title: "October Tail Latency Postmortem",
              hops: 2,
              relations: ["MENTIONS", "RELATES_TO"],
              path_score: 0.84,
              is_contradiction: false,
            },
            {
              seed_id: "s2",
              seed_title: "Seed Without Relations",
              bridge_id: null,
              bridge_title: "Fallback Bridge",
              target_id: "t3",
              target_title: "Direct Fallback Target",
              hops: 2,
              relations: [],
              path_score: 0.51,
              is_contradiction: false,
            },
            {
              seed_id: "s3",
              seed_title: "Direct Seed",
              bridge_id: null,
              bridge_title: null,
              target_id: "t4",
              target_title: "One Hop Target",
              hops: 1,
              relations: ["DEPENDS_ON"],
              path_score: 0.65,
              is_contradiction: false,
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("2 direct · 3 expanded · 3 paths")).toBeTruthy();
    expect(screen.getByText("WAL Contention · 2")).toBeTruthy();
    expect(screen.getByText("CONTRADICTS")).toBeTruthy();
    expect(screen.getByText("Disable Auto-Checkpoint")).toBeTruthy();
    expect(screen.getByText("October Tail Latency Postmortem")).toBeTruthy();
    expect(screen.getAllByText("2-Hop")).toHaveLength(2);
    expect(screen.getByText("1-Hop")).toBeTruthy();
    expect(screen.getAllByText(/—\[LINKS\]→/)).toHaveLength(2);
  });
});
