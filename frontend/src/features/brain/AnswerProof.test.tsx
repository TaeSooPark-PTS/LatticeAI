import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { latticeApi, type EvidenceAction } from "@/api/client";
import { AnswerProofCard, EvidenceActionRow, InlineCitationMarkers, parseSourceNode } from "./AnswerProof";
import type { Message } from "./types";

type Proof = NonNullable<Message["proof"]>;

function citation(overrides: Partial<Proof["citations"][number]> = {}): Proof["citations"][number] {
  return {
    id: "node-1",
    source: "메모",
    title: "예산 계획",
    snippet: "3분기 예산은 1억",
    matchedTerms: ["예산"],
    confidence: "high",
    score: 0.92,
    locator: "가이드 > 2장 · p.4",
    ...overrides,
  };
}

function proof(overrides: Partial<Proof> = {}): Proof {
  return {
    query: "예산이 얼마였지",
    model: "qwen",
    provenAcrossModels: true,
    citations: [citation()],
    ...overrides,
  };
}

function mockGraphNode(data: unknown, ok = true, error?: string, status = ok ? 200 : 503) {
  return vi.spyOn(latticeApi, "graphNode").mockResolvedValue({
    ok, status, source: ok ? "live" : "unavailable", data, error,
  } as never);
}

beforeAll(() => {
  // jsdom has no scrollIntoView; the marker only needs it to exist.
  Element.prototype.scrollIntoView = vi.fn();
});

describe("parseSourceNode", () => {
  it("builds the source node with provenance from known metadata keys", () => {
    const node = parseSourceNode({
      node: {
        title: "예산 계획",
        type: "Document",
        summary: "본문 텍스트",
        metadata: { source: "upload", relative_path: "docs/budget.md", irrelevant: "x" },
      },
    });
    expect(node).toEqual({
      title: "예산 계획",
      type: "Document",
      summary: "본문 텍스트",
      provenance: [
        { key: "source", value: "upload" },
        { key: "relative_path", value: "docs/budget.md" },
      ],
    });
  });

  it("returns null when the payload has no node record", () => {
    expect(parseSourceNode(null)).toBeNull();
    expect(parseSourceNode({})).toBeNull();
    expect(parseSourceNode({ node: "text" })).toBeNull();
  });

  it("tolerates a node without metadata", () => {
    const node = parseSourceNode({ node: { label: "라벨", content: "내용" } });
    expect(node?.title).toBe("라벨");
    expect(node?.summary).toBe("내용");
    expect(node?.provenance).toEqual([]);
  });
});

describe("AnswerProofCard", () => {
  it("shows the proven model line, citation details and why it matched", () => {
    render(
      <AnswerProofCard
        language="ko"
        proof={proof({
          citations: [citation({ matchedTerms: ["예산", "3분기", "계획", "지출", "다섯째"] })],
        })}
        messageId="m1"
      />,
    );
    expect(screen.getByText(/qwen/).textContent).toContain("qwen");
    expect(screen.getByTestId("citation-locator").textContent).toBe("가이드 > 2장 · p.4");
    expect(screen.getByText("3분기 예산은 1억")).toBeTruthy();
    // Confidence pill plus at most four matched terms.
    expect(document.querySelector(".brain-citation-confidence.is-high")).toBeTruthy();
    expect(document.querySelectorAll(".brain-citation-why mark").length).toBe(4);
    expect(screen.queryByText("다섯째")).toBeNull();
    // No evidence action row without a callback.
    expect(screen.queryByTestId("evidence-actions-open")).toBeNull();
  });

  it("degrades honestly: pending model, no locator, query as snippet, no matches", () => {
    render(
      <AnswerProofCard
        language="ko"
        proof={proof({
          provenAcrossModels: false,
          citations: [citation({ locator: "", snippet: "", matchedTerms: [] })],
        })}
        messageId="m1"
      />,
    );
    expect(screen.queryByTestId("citation-locator")).toBeNull();
    // Empty snippet falls back to the original query.
    expect(screen.getByText("예산이 얼마였지")).toBeTruthy();
    expect(screen.getByText("저장된 기억에서 직접 찾음")).toBeTruthy();
  });

  it("admits when no source backs the answer", () => {
    render(
      <AnswerProofCard language="ko" proof={proof({ citations: [] })} messageId="m1" onUseEvidence={() => {}} />,
    );
    expect(
      screen.getByText("아직 연결된 출처가 없습니다. 기억이 인덱싱되면 여기에 표시됩니다."),
    ).toBeTruthy();
    expect(document.querySelector(".brain-answer-proof-count")).toBeNull();
    // Even with a callback there is nothing to act on.
    expect(screen.queryByTestId("evidence-actions-open")).toBeNull();
  });

  it("offers evidence actions when a callback and citations exist", () => {
    render(
      <AnswerProofCard language="ko" proof={proof()} messageId="m1" onUseEvidence={() => {}} />,
    );
    expect(screen.getByTestId("evidence-actions-open")).toBeTruthy();
  });
});

describe("EvidenceActionRow", () => {
  function evidenceAction(overrides: Partial<EvidenceAction> = {}): EvidenceAction {
    return {
      id: "act-1",
      kind: "write_file",
      label: { ko: "정리본 만들기", en: "Build a summary" },
      prompt: "정리해서 파일로 만들어줘",
      source_ids: ["node-1"],
      ...overrides,
    };
  }

  function mockEvidenceActions(result: { ok: boolean; data?: unknown; error?: string }) {
    return vi.spyOn(latticeApi, "evidenceActions").mockResolvedValue({
      ok: result.ok,
      status: result.ok ? 200 : 503,
      source: result.ok ? "live" : "unavailable",
      data: result.data ?? {},
      error: result.error,
    } as never);
  }

  it("loads on click, shows a loading line, then lists actions with an optional suggested path", async () => {
    let release: (value: unknown) => void = () => {};
    const spy = vi.spyOn(latticeApi, "evidenceActions").mockReturnValue(
      new Promise((resolve) => { release = resolve; }) as never,
    );
    const onUseEvidence = vi.fn();
    render(
      <EvidenceActionRow language="ko" query="예산이 얼마였지" sourceIds={["node-1"]} onUseEvidence={onUseEvidence} />,
    );
    await userEvent.click(screen.getByTestId("evidence-actions-open"));
    expect(spy).toHaveBeenCalledWith("예산이 얼마였지", ["node-1"], "ko");
    expect(screen.getByRole("status").textContent).toBe("만들 수 있는 것을 찾는 중…");

    release({
      ok: true,
      status: 200,
      source: "live",
      data: {
        sources: [],
        missing: [],
        reason: "",
        actions: [
          evidenceAction({ id: "a1", suggested_path: "out/summary.md" }),
          evidenceAction({ id: "a2", label: { ko: "두번째", en: "Second" }, suggested_path: undefined }),
        ],
      },
    });

    const list = await screen.findByTestId("evidence-actions");
    expect(list.textContent).toContain("이 근거로 만들 수 있는 것");
    const first = screen.getByTestId("evidence-action-a1");
    expect(first.textContent).toBe("정리본 만들기");
    expect(first.getAttribute("title")).toBe("out/summary.md");
    const second = screen.getByTestId("evidence-action-a2");
    expect(second.textContent).toBe("두번째");
    expect(second.getAttribute("title")).toBeNull();

    await userEvent.click(first);
    expect(onUseEvidence).toHaveBeenCalledWith("정리해서 파일로 만들어줘");
  });

  it("shows the english label when the language is en", async () => {
    mockEvidenceActions({
      ok: true,
      data: { sources: [], missing: [], reason: "", actions: [evidenceAction()] },
    });
    render(
      <EvidenceActionRow language="en" query="q" sourceIds={["node-1"]} onUseEvidence={() => {}} />,
    );
    await userEvent.click(screen.getByTestId("evidence-actions-open"));
    const button = await screen.findByTestId("evidence-action-act-1");
    expect(button.textContent).toBe("Build a summary");
  });

  it("shows the default unavailable copy when the answer has no follow-ups", async () => {
    mockEvidenceActions({ ok: true, data: { sources: [], missing: [], reason: "", actions: [] } });
    render(<EvidenceActionRow language="ko" query="q" sourceIds={[]} onUseEvidence={() => {}} />);
    await userEvent.click(screen.getByTestId("evidence-actions-open"));
    const notice = await screen.findByText("이 답변의 출처로는 바로 만들 수 있는 게 없습니다.");
    expect(notice.className).toContain("is-error");
  });

  it("shows the server's reason when the evidence request itself failed", async () => {
    mockEvidenceActions({ ok: false, error: "index_not_ready" });
    render(<EvidenceActionRow language="ko" query="q" sourceIds={[]} onUseEvidence={() => {}} />);
    await userEvent.click(screen.getByTestId("evidence-actions-open"));
    await screen.findByText("index_not_ready");
  });

  it("falls back to the default copy when a failed request carries no reason", async () => {
    mockEvidenceActions({ ok: false });
    render(<EvidenceActionRow language="ko" query="q" sourceIds={[]} onUseEvidence={() => {}} />);
    await userEvent.click(screen.getByTestId("evidence-actions-open"));
    await screen.findByText("이 답변의 출처로는 바로 만들 수 있는 게 없습니다.");
  });
});

describe("SourceChunkModal", () => {
  it("opens the cited chunk with its stored text and provenance", async () => {
    mockGraphNode({
      node: {
        title: "노드 제목",
        type: "Document",
        summary: "저장된 본문",
        metadata: { source: "web", filename: "a.md" },
      },
    });
    render(<AnswerProofCard language="ko" proof={proof()} messageId="m1" />);
    await userEvent.click(screen.getByTestId("citation-open"));

    const modal = await screen.findByTestId("source-chunk-modal");
    expect(screen.getByTestId("source-chunk-text").textContent).toBe("저장된 본문");
    const provenance = screen.getByTestId("source-chunk-provenance");
    expect(provenance.textContent).toContain("web");
    expect(provenance.textContent).toContain("a.md");
    expect(modal.querySelector(".source-chunk-type")!.textContent).toBe("Document");
    expect(modal.getAttribute("aria-label")).toContain("노드 제목");
  });

  it("shows a loading line first and can be closed from the header", async () => {
    let release: (value: unknown) => void = () => {};
    vi.spyOn(latticeApi, "graphNode").mockReturnValue(
      new Promise((resolve) => { release = resolve; }) as never,
    );
    render(<AnswerProofCard language="ko" proof={proof()} messageId="m1" />);
    await userEvent.click(screen.getByTestId("citation-open"));
    expect(screen.getByRole("status").textContent).toContain("원문을 불러오는 중");

    release({ ok: true, status: 200, source: "live", data: { node: { title: "늦은 응답", summary: "본문" } } });
    await screen.findByTestId("source-chunk-text");

    fireEvent.click(screen.getByRole("button", { name: "원문 닫기" }));
    expect(screen.queryByTestId("source-chunk-modal")).toBeNull();
  });

  it("falls back to the citation title and hides extras for a sparse node", async () => {
    mockGraphNode({ node: { title: "", type: "", summary: "", metadata: {} } });
    render(<AnswerProofCard language="ko" proof={proof()} messageId="m1" />);
    await userEvent.click(screen.getByTestId("citation-open"));

    const modal = await screen.findByTestId("source-chunk-modal");
    expect(modal.getAttribute("aria-label")).toContain("예산 계획");
    await screen.findByText("이 출처에는 저장된 본문이 없어요.");
    expect(screen.queryByTestId("source-chunk-provenance")).toBeNull();
    expect(modal.querySelector(".source-chunk-type")).toBeNull();
  });

  it("tells the truth when the citation is not a graph node", async () => {
    mockGraphNode({}, false, "recall_only");
    render(<AnswerProofCard language="ko" proof={proof()} messageId="m1" />);
    await userEvent.click(screen.getByTestId("citation-open"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("recall_only");
  });

  it("reports the HTTP status when the failure has no message at all", async () => {
    mockGraphNode({}, false, undefined, 404);
    render(<AnswerProofCard language="ko" proof={proof()} messageId="m1" />);
    await userEvent.click(screen.getByTestId("citation-open"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("404");
  });

  it("shows an empty reason for a transport-level failure without a status", async () => {
    mockGraphNode({}, false, undefined, 0);
    render(<AnswerProofCard language="ko" proof={proof()} messageId="m1" />);
    await userEvent.click(screen.getByTestId("citation-open"));
    const alert = await screen.findByRole("alert");
    expect(alert).toBeTruthy();
    expect(alert.textContent).not.toContain("404");
  });

  it("closes on a true backdrop press but not on presses inside the dialog", async () => {
    mockGraphNode({ node: { title: "닫기 실험", summary: "본문" } });
    render(<AnswerProofCard language="ko" proof={proof()} messageId="m1" />);
    await userEvent.click(screen.getByTestId("citation-open"));
    await screen.findByTestId("source-chunk-modal");

    fireEvent.mouseDown(screen.getByTestId("source-chunk-modal"));
    expect(screen.queryByTestId("source-chunk-modal")).toBeTruthy();

    fireEvent.mouseDown(document.querySelector(".file-preview-backdrop")!);
    expect(screen.queryByTestId("source-chunk-modal")).toBeNull();
  });

  it("ignores a response that lands after the modal unmounted", async () => {
    let release: (value: unknown) => void = () => {};
    vi.spyOn(latticeApi, "graphNode").mockReturnValue(
      new Promise((resolve) => { release = resolve; }) as never,
    );
    const { unmount } = render(<AnswerProofCard language="ko" proof={proof()} messageId="m1" />);
    await userEvent.click(screen.getByTestId("citation-open"));
    unmount();
    release({ ok: true, status: 200, source: "live", data: { node: { title: "유령", summary: "x" } } });
    await Promise.resolve();
    // Nothing to assert visually — the guard just must not warn or crash.
    expect(screen.queryByTestId("source-chunk-modal")).toBeNull();
  });
});

describe("InlineCitationMarkers", () => {
  it("jumps focus to the matching evidence row", async () => {
    render(
      <div>
        <InlineCitationMarkers language="ko" proof={proof()} messageId="m1" />
        <AnswerProofCard language="ko" proof={proof()} messageId="m1" />
      </div>,
    );
    const marker = screen.getByRole("button", { name: "출처 1로 이동" });
    await userEvent.click(marker);
    const row = document.getElementById("m1-cite-node-1")!;
    expect(document.activeElement).toBe(row);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("stays calm when the evidence card is not in the document", async () => {
    render(<InlineCitationMarkers language="ko" proof={proof()} messageId="io-없음" />);
    const marker = screen.getByRole("button", { name: "출처 1로 이동" });
    await userEvent.click(marker);
    // No matching row: focus stays on the marker instead of jumping away.
    expect(document.activeElement).toBe(marker);
  });
});
