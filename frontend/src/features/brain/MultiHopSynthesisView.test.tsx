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

  it("filters paths by hop count or conflict flag and invokes onSelectNode for bridges, conflicts, and chain nodes", async () => {
    const selected: Array<[string, string]> = [];
    const { getByTestId, getByRole, queryByText } = render(
      <MultiHopSynthesisView
        language="en"
        onSelectNode={(id, title) => selected.push([id, title])}
        data={{
          synthesis: {
            direct_hits: 2,
            multi_hop_hits: 1,
            bridge_concepts: [
              { id: "b1", title: "Bridge One", node_type: "Concept", bridged_targets: 2 },
            ],
            contradictions: [
              {
                source_id: "s1",
                source_title: "Source Conflict",
                target_id: "c1",
                target_title: "Target Conflict",
                relation: "CONTRADICTS",
              },
            ],
          },
          reasoning_paths: [
            {
              seed_id: "s1",
              seed_title: "Seed A",
              bridge_id: "b1",
              bridge_title: "Bridge One",
              target_id: "t1",
              target_title: "Two Hop Target",
              hops: 2,
              relations: ["DEPENDS_ON", "PROVES"],
              path_score: 0.9,
              is_contradiction: false,
            },
            {
              seed_id: "s2",
              seed_title: "Seed B",
              bridge_id: null,
              bridge_title: null,
              target_id: "c1",
              target_title: "Conflict Target",
              hops: 1,
              relations: ["CONTRADICTS"],
              path_score: 0,
              is_contradiction: true,
            },
          ],
        }}
      />,
    );

    getByTestId("bridge-chip-b1").click();
    getByRole("button", { name: "Jump to Source Conflict" }).click();
    getByRole("button", { name: "Jump to Two Hop Target" }).click();
    expect(selected).toEqual([
      ["b1", "Bridge One"],
      ["s1", "Source Conflict"],
      ["t1", "Two Hop Target"],
    ]);

    // Filter to 2+ hops -> 1-hop Conflict Target hides; filter to conflicts -> Two Hop Target hides; filter all -> both return.
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.click(getByTestId("synthesis-filter-multiHop"));
    expect(queryByText("Conflict Target")).toBeNull();
    fireEvent.click(getByTestId("synthesis-filter-conflicts"));
    expect(queryByText("Two Hop Target")).toBeNull();
    expect(queryByText("Conflict Target")).toBeTruthy();
    fireEvent.click(getByTestId("synthesis-filter-all"));
    expect(queryByText("Two Hop Target")).toBeTruthy();
  });

  it("hides filter buttons when only 1-hop non-conflict paths exist and omits multiHop pill when only 1-hop conflict paths exist", () => {
    const plain = render(
      <MultiHopSynthesisView
        language="en"
        data={{
          reasoning_paths: [
            {
              seed_id: "s1",
              seed_title: "Seed",
              bridge_id: null,
              bridge_title: null,
              target_id: "t1",
              target_title: "Plain 1-Hop",
              hops: 1,
              relations: ["LINKS"],
              path_score: 0.7,
              is_contradiction: false,
            },
          ],
        }}
      />,
    );
    expect(plain.queryByTestId("synthesis-filter-all")).toBeNull();
    plain.unmount();

    const conflictOnly = render(
      <MultiHopSynthesisView
        language="en"
        data={{
          reasoning_paths: [
            {
              seed_id: "s1",
              seed_title: "Seed",
              bridge_id: null,
              bridge_title: null,
              target_id: "t2",
              target_title: "Conflict 1-Hop",
              hops: 1,
              relations: ["CONTRADICTS"],
              path_score: 0.8,
              is_contradiction: true,
            },
          ],
        }}
      />,
    );
    expect(conflictOnly.getByTestId("synthesis-filter-conflicts")).toBeTruthy();
    expect(conflictOnly.queryByTestId("synthesis-filter-multiHop")).toBeNull();
  });
});
