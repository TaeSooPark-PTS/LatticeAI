import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { t } from "@/i18n";
import { fail, ok, stubApi } from "@/test/renderPage";
import type { BrainReadiness, KnowledgeGraphModel, MemoryFragment } from "../types";
import { useBrainIngestion } from "./useBrainIngestion";

/**
 * Everything that feeds the Brain: files, folders, notes, web pages and the
 * begin/complete/fail ingestion state machine with its emergence events.
 *
 * The folder picker is mocked at the module boundary — it probes the desktop
 * shell and `window.showDirectoryPicker`, which is environment, not behaviour
 * under test. `browserFolderNameFromFiles` stays real.
 */

const { pickFolderMock } = vi.hoisted(() => ({ pickFolderMock: vi.fn() }));
vi.mock("@/lib/folderPicker", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/folderPicker")>();
  return { ...actual, pickFolder: pickFolderMock };
});

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

const readiness: BrainReadiness = {
  score: 40,
  state: "forming",
  depth: 3,
  titleKey: "brain.readiness.forming",
  actionKey: "brain.readiness.grow",
  source: "frontend_fallback",
  signals: { memoryCount: 2, conceptCount: 1, relationshipCount: 0, healthySources: 1 },
};

const graphModel: KnowledgeGraphModel = {
  nodes: [{ id: "node-a", label: "A", type: "Concept", summary: "", importance: 0.6 }],
  edges: [],
};

function setup(
  overrides: Partial<Parameters<typeof useBrainIngestion>[0]> = {},
  api: Parameters<typeof stubApi>[0] = {},
) {
  stubApi(api);
  const requestDetails = vi.fn();
  const setLastRecallQuery = vi.fn();
  const feedback: Array<string | null> = [];
  const brainStates: string[] = [];
  const props = {
    language: "ko" as const,
    brainReadiness: readiness,
    memoryFragments: [] as MemoryFragment[],
    graphModel,
    memoriesFetched: true,
    graphFetched: true,
    requestDetails,
    setLastRecallQuery: setLastRecallQuery as never,
    setMemoryFeedback: ((value: unknown) => {
      feedback.push(typeof value === "function" ? "(updater)" : (value as string | null));
    }) as never,
    onBrainChange: (state: string) => brainStates.push(state),
    ...overrides,
  };
  const rendered = renderHook(() => useBrainIngestion(props), { wrapper });
  return { ...rendered, requestDetails, setLastRecallQuery, feedback, brainStates };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ingestion state machine", () => {
  it("begin → complete measures what actually became new, and pulses synthesis", async () => {
    const { result, requestDetails } = setup({}, {
      memoryManager: ok({ brain_readiness: {
        state: "alive", depth: 5, score: 90,
        signals: { memory_count: 5, concept_count: 3, relationship_count: 2, healthy_sources: 2 },
      } }),
      graphPreview: ok({ nodes: [{ id: "node-a", title: "A" }, { id: "node-b", title: "B" }] }),
    });

    act(() => {
      result.current.beginIngestion("note", "새 메모");
    });
    expect(requestDetails).toHaveBeenCalled();
    expect(result.current.ingestionStates.note).toMatchObject({ stage: "preparing", label: "새 메모" });

    let counts: { memories: number; entities: number } | undefined;
    await act(async () => {
      counts = await result.current.completeIngestion("note", {
        node_id: "node-b", chunk_count: 4, duplicate: false, provenance_id: "prov-1", indexed_nodes: ["node-a"],
      });
    });

    expect(counts).toEqual({ memories: 5, entities: 3 });
    expect(result.current.ingestionStates.note).toMatchObject({
      stage: "complete",
      newMemories: 3,
      newEntities: 2,
      nodeIds: ["node-b"],
      chunkCount: 4,
      duplicate: false,
      provenanceId: "prov-1",
    });
    expect(result.current.emergenceEvents[0]).toMatchObject({
      sourceType: "note", label: "새 메모", newMemories: 3, newEntities: 2, nodeIds: ["node-b"],
    });

    expect(result.current.synthesisActive).toBe(true);
    act(() => {
      vi.advanceTimersByTime(1700);
    });
    expect(result.current.synthesisActive).toBe(false);
  });

  it("complete without a begin still reports the emergence but keeps no tile state", async () => {
    const { result } = setup({ memoriesFetched: false, graphFetched: false });

    await act(async () => {
      await result.current.completeIngestion("web", { node_id: "vapor" });
    });

    expect(result.current.ingestionStates.web).toBeNull();
    expect(result.current.emergenceEvents[0]).toMatchObject({
      sourceType: "web", label: "", newMemories: 0, newEntities: 1, nodeIds: ["vapor"],
    });
  });

  it("keeps the readiness and graph it already had when the refresh calls fail", async () => {
    const { result } = setup({}, {
      memoryManager: fail("down", {}),
      graphPreview: fail("down", {}),
    });

    act(() => {
      result.current.beginIngestion("chat", "질문");
    });
    let counts: { memories: number; entities: number } | undefined;
    await act(async () => {
      counts = await result.current.completeIngestion("chat");
    });

    expect(counts).toEqual({ memories: 2, entities: 1 });
    expect(result.current.ingestionStates.chat).toMatchObject({ stage: "complete", newMemories: 0, newEntities: 0 });
  });

  it("failIngestion marks the tile and is a no-op without a begin", () => {
    const { result } = setup();

    act(() => {
      result.current.failIngestion("note", "이유 없음");
    });
    expect(result.current.ingestionStates.note).toBeNull();

    act(() => {
      result.current.beginIngestion("note", "메모");
      result.current.failIngestion("note", "터짐");
    });
    expect(result.current.ingestionStates.note).toMatchObject({ stage: "error", error: "터짐" });
  });

  it("resetChatIngestion clears only the chat tile", async () => {
    const { result } = setup();
    act(() => {
      result.current.beginIngestion("chat", "질문");
      result.current.beginIngestion("file", "문서.pdf");
    });

    act(() => {
      result.current.resetChatIngestion();
    });
    expect(result.current.ingestionStates.chat).toBeNull();
    expect(result.current.ingestionStates.file).toMatchObject({ stage: "preparing" });
  });
});

describe("uploadDocument", () => {
  it("uploads, records extraction quality and refreshes the recall query", async () => {
    const { result, setLastRecallQuery, feedback, brainStates } = setup({}, {
      uploadDocument: ok({
        node_id: "doc-1", chunk_count: 2,
        extraction_quality: { score: 0.9, level: "high", reasons: [] },
      }),
    });
    const file = new File(["내용"], "노트.pdf");

    await act(async () => {
      await result.current.uploadDocument(file);
    });

    expect(brainStates).toContain("recalling");
    expect(feedback).toContain(t("ko", "brain.upload.pending", { name: "노트.pdf" }));
    expect(feedback.at(-1)).toBe(t("ko", "brain.upload.saved", { name: "노트.pdf" }));
    expect(setLastRecallQuery).toHaveBeenCalledWith("노트.pdf");
    expect(result.current.ingestionStates.file).toMatchObject({ stage: "complete", extraction: { level: "high" } });
    expect(result.current.uploadingDocument).toBe(false);
  });

  it("reports an upload failure with the server reason or a plain fallback", async () => {
    const { result, feedback } = setup({}, { uploadDocument: fail("형식을 읽지 못함", null) });
    await act(async () => {
      await result.current.uploadDocument(new File([""], "깨진.hwp"));
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.upload.failed", { reason: "형식을 읽지 못함" }));
    expect(result.current.ingestionStates.file).toMatchObject({ stage: "error", error: "형식을 읽지 못함" });

    stubApi({ uploadDocument: { ok: false, status: 503, source: "unavailable", data: null } });
    await act(async () => {
      await result.current.uploadDocument(new File([""], "또.hwp"));
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.upload.failed", { reason: "unavailable" }));
  });

  it("ignores a second upload while one is in flight", async () => {
    let release!: (value: unknown) => void;
    const { result } = setup({}, {
      uploadDocument: () => new Promise((resolve) => { release = resolve; }),
    });

    let first!: Promise<void>;
    act(() => {
      first = result.current.uploadDocument(new File(["1"], "하나.txt"));
    });
    await waitFor(() => expect(result.current.uploadingDocument).toBe(true));

    await act(async () => {
      await result.current.uploadDocument(new File(["2"], "둘.txt"));
    });
    expect(latticeApi.uploadDocument).toHaveBeenCalledTimes(1);

    await act(async () => {
      release(ok({}));
      await first;
    });
    expect(result.current.uploadingDocument).toBe(false);
  });
});

describe("connectFolder", () => {
  it("does nothing for a blank path", async () => {
    const { result, feedback } = setup();
    await act(async () => {
      await result.current.connectFolder("   ");
    });
    expect(latticeApi.connectFolder).not.toHaveBeenCalled();
    expect(feedback).toEqual([]);
  });

  it("connects a real path and completes the folder tile", async () => {
    const { result, setLastRecallQuery, feedback } = setup({}, {
      connectFolder: ok({ node_id: "folder-1" }),
    });
    await act(async () => {
      await result.current.connectFolder(" /Users/me/문서 ");
    });
    expect(latticeApi.connectFolder).toHaveBeenCalledWith("/Users/me/문서");
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.folder.saved", { path: "/Users/me/문서" }));
    expect(setLastRecallQuery).toHaveBeenCalledWith("/Users/me/문서");
    expect(result.current.ingestionStates.folder).toMatchObject({ stage: "complete" });
  });

  it("reports a folder failure with the reason or a plain fallback", async () => {
    const { result, feedback } = setup({}, { connectFolder: fail("권한 없음", {}) });
    await act(async () => {
      await result.current.connectFolder("/root/금지");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.folder.failed", { reason: "권한 없음" }));
    expect(result.current.ingestionStates.folder).toMatchObject({ stage: "error" });

    stubApi({ connectFolder: { ok: false, status: 503, source: "unavailable", data: {} } });
    await act(async () => {
      await result.current.connectFolder("/root/다시");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.folder.failed", { reason: "unavailable" }));
  });
});

describe("pickFolder", () => {
  it("says nothing when the user cancels the picker", async () => {
    pickFolderMock.mockResolvedValue({ kind: "cancelled" });
    const { result, feedback } = setup();
    await act(async () => {
      await result.current.pickFolder();
    });
    expect(feedback).toEqual([]);
  });

  it("explains when no folder route exists in this shell", async () => {
    pickFolderMock.mockResolvedValue({ kind: "unavailable" });
    const { result, feedback } = setup();
    await act(async () => {
      await result.current.pickFolder();
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.folder.unavailable"));
  });

  it("routes a desktop path selection through connectFolder", async () => {
    pickFolderMock.mockResolvedValue({ kind: "path", path: "/Users/me/notes" });
    const { result } = setup({}, { connectFolder: ok({}) });
    await act(async () => {
      await result.current.pickFolder();
    });
    expect(latticeApi.connectFolder).toHaveBeenCalledWith("/Users/me/notes");
  });

  it("uploads browser-read files one by one and reports the batch", async () => {
    const good = new File(["good"], "good.md");
    const bad = new File(["bad"], "bad.bin");
    pickFolderMock.mockResolvedValue({ kind: "files", name: "내폴더", files: [good, bad] });
    const uploadDocument = vi.fn()
      .mockResolvedValueOnce(ok({ node_id: "up-1" }))
      .mockResolvedValueOnce(fail("깨짐", null));
    const { result, setLastRecallQuery, feedback } = setup({}, { uploadDocument });

    await act(async () => {
      await result.current.pickFolder();
    });

    expect(uploadDocument).toHaveBeenCalledTimes(2);
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.folder.browserSaved", { name: "내폴더", count: 1 }));
    expect(setLastRecallQuery).toHaveBeenCalledWith("내폴더");
    expect(result.current.ingestionStates.folder).toMatchObject({ stage: "complete" });
  });

  it("derives the folder name from the files when the handle has none", async () => {
    const rel = new File(["a"], "a.txt");
    Object.defineProperty(rel, "webkitRelativePath", { value: "MyFolder/a.txt" });
    pickFolderMock.mockResolvedValue({ kind: "files", name: "", files: [rel] });
    const { result, feedback } = setup({}, { uploadDocument: ok({}) });

    await act(async () => {
      await result.current.pickFolder();
    });

    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.folder.browserSaved", { name: "MyFolder", count: 1 }));
  });

  it("an empty folder selection fails before anything begins", async () => {
    pickFolderMock.mockResolvedValue({ kind: "files", name: "빈폴더", files: [] });
    const { result, feedback } = setup();
    await act(async () => {
      await result.current.pickFolder();
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.folder.empty"));
    expect(result.current.ingestionStates.folder).toBeNull();
  });

  it("a folder whose files all fail to upload ends in an honest error", async () => {
    pickFolderMock.mockResolvedValue({ kind: "files", name: "망한폴더", files: [new File(["x"], "x.bin")] });
    const { result, feedback } = setup({}, { uploadDocument: fail("전부 실패", null) });
    await act(async () => {
      await result.current.pickFolder();
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.folder.empty"));
    expect(result.current.ingestionStates.folder).toMatchObject({ stage: "error", error: "empty" });
  });
});

describe("ingestNote and ingestWeb", () => {
  it("ignores an empty note", async () => {
    const { result } = setup();
    await act(async () => {
      await result.current.ingestNote("   ");
    });
    expect(latticeApi.ingestNote).not.toHaveBeenCalled();
  });

  it("saves a note and remembers it as the recall query", async () => {
    const { result, setLastRecallQuery, feedback } = setup({}, { ingestNote: ok({ node_id: "note-1" }) });
    await act(async () => {
      await result.current.ingestNote(" 오늘 회의: 출시일 확정 ");
    });
    expect(latticeApi.ingestNote).toHaveBeenCalledWith("오늘 회의: 출시일 확정", "오늘 회의: 출시일 확정");
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.note.saved"));
    expect(setLastRecallQuery).toHaveBeenCalledWith("오늘 회의: 출시일 확정");
    expect(result.current.ingestionStates.note).toMatchObject({ stage: "complete" });
  });

  it("reports a note failure with the reason or a plain fallback", async () => {
    const { result, feedback } = setup({}, { ingestNote: fail("저장 공간 부족", null) });
    await act(async () => {
      await result.current.ingestNote("메모");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.note.failed", { reason: "저장 공간 부족" }));
    expect(result.current.ingestionStates.note).toMatchObject({ stage: "error" });

    stubApi({ ingestNote: { ok: false, status: 503, source: "unavailable", data: null } });
    await act(async () => {
      await result.current.ingestNote("메모");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.note.failed", { reason: "unavailable" }));
  });

  it("ignores an empty url", async () => {
    const { result } = setup();
    await act(async () => {
      await result.current.ingestWeb("   ");
    });
    expect(latticeApi.browserReadUrl).not.toHaveBeenCalled();
  });

  it("saves a page the browser actually captured", async () => {
    const { result, setLastRecallQuery, feedback } = setup({}, {
      browserReadUrl: ok({ status: "ok", node_id: "web-1" }),
    });
    await act(async () => {
      await result.current.ingestWeb(" https://example.com/글 ");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.web.saved", { url: "https://example.com/글" }));
    expect(setLastRecallQuery).toHaveBeenCalledWith("https://example.com/글");
    expect(result.current.ingestionStates.web).toMatchObject({ stage: "complete" });
  });

  it("a transport failure reports the server error", async () => {
    const { result, feedback } = setup({}, { browserReadUrl: fail("차단됨", null) });
    await act(async () => {
      await result.current.ingestWeb("https://example.com");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.web.failed", { reason: "차단됨" }));
    expect(result.current.ingestionStates.web).toMatchObject({ stage: "error", error: "차단됨" });
  });

  it("an empty capture is a failure with the backend's detail, never a fake save", async () => {
    const { result, feedback } = setup({}, {
      browserReadUrl: ok({ status: "empty", detail: "본문을 찾지 못했어요" }),
    });
    await act(async () => {
      await result.current.ingestWeb("https://example.com/빈페이지");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.web.failed", { reason: "본문을 찾지 못했어요" }));
    expect(result.current.ingestionStates.web).toMatchObject({ stage: "error" });
  });

  it("a shapeless capture response falls back to a plain unavailable", async () => {
    const { result, feedback } = setup({}, { browserReadUrl: ok({}) });
    await act(async () => {
      await result.current.ingestWeb("https://example.com/이상함");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.ingest.web.failed", { reason: "unavailable" }));
  });
});
