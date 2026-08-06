import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BrainIngestionDock, BrainIngestionPanel, IngestionTimelineSection } from "./IngestionPanels";
import type { EmergenceEvent, IngestionSourceType, IngestionState } from "./types";

const EMPTY_STATES: Record<IngestionSourceType, IngestionState | null> = {
  chat: null,
  file: null,
  folder: null,
  note: null,
  web: null,
};

function ingestion(overrides: Partial<IngestionState> = {}): IngestionState {
  return {
    sourceType: "file",
    label: "보고서.pdf",
    stage: "parsing",
    startedAt: 0,
    completedAt: null,
    newMemories: 0,
    newEntities: 0,
    ...overrides,
  };
}

function panelHandlers() {
  return {
    onUploadDocument: vi.fn(),
    onPickFolder: vi.fn(),
    onConnectFolder: vi.fn(),
    onIngestNote: vi.fn(),
    onIngestWeb: vi.fn(),
  };
}

function renderPanel(
  overrides: Partial<React.ComponentProps<typeof BrainIngestionPanel>> = {},
) {
  const handlers = panelHandlers();
  const utils = render(
    <BrainIngestionPanel
      language="ko"
      uploadingDocument={false}
      ingestionStates={EMPTY_STATES}
      {...handlers}
      {...overrides}
    />,
  );
  return { ...utils, ...handlers };
}

describe("BrainIngestionPanel", () => {
  it("shows a resting call-to-action on every tile before any ingestion", () => {
    renderPanel();
    expect(screen.getByText("선택해서 바로 넣기")).toBeTruthy();
    expect(screen.getByText("경로를 넣고 Enter")).toBeTruthy();
    expect(screen.getByText("내용을 붙여넣고 Enter")).toBeTruthy();
    expect(screen.getByText("URL을 넣고 Enter")).toBeTruthy();
    expect(document.querySelector(".is-ingesting")).toBeNull();
    expect(document.querySelector(".is-failed")).toBeNull();
    expect(document.querySelector(".is-emerged")).toBeNull();
  });

  it("uploads a picked file and clears the input for the next one", async () => {
    const { onUploadDocument } = renderPanel();
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    const file = new File(["내용"], "보고서.pdf", { type: "application/pdf" });
    await userEvent.upload(input, file);
    expect(onUploadDocument).toHaveBeenCalledWith(file);
    expect(input.value).toBe("");
  });

  it("ignores a change event that carries no file", () => {
    const { onUploadDocument } = renderPanel();
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, "files", { value: null, configurable: true });
    fireEvent.change(input);
    expect(onUploadDocument).not.toHaveBeenCalled();
  });

  it("disables the file tile while an upload is in flight", () => {
    renderPanel({ uploadingDocument: true });
    expect(screen.getByText("넣는 중")).toBeTruthy();
    expect(document.querySelector(".brain-ingest-tile.is-disabled")).toBeTruthy();
    expect(document.querySelector<HTMLInputElement>('input[type="file"]')!.disabled).toBe(true);
  });

  it("submits folder, note and web sources and clears each input", () => {
    const { onConnectFolder, onIngestNote, onIngestWeb, onPickFolder } = renderPanel();
    const [folderInput, noteInput, webInput] = Array.from(
      document.querySelectorAll<HTMLInputElement>('.brain-ingest-tile input:not([type="file"])'),
    );

    fireEvent.change(folderInput, { target: { value: "~/Documents" } });
    fireEvent.submit(folderInput.closest("form")!);
    expect(onConnectFolder).toHaveBeenCalledWith("~/Documents");
    expect(folderInput.value).toBe("");

    fireEvent.change(noteInput, { target: { value: "오늘 배운 것" } });
    fireEvent.submit(noteInput.closest("form")!);
    expect(onIngestNote).toHaveBeenCalledWith("오늘 배운 것");
    expect(noteInput.value).toBe("");

    fireEvent.change(webInput, { target: { value: "https://lattice.dev" } });
    fireEvent.submit(webInput.closest("form")!);
    expect(onIngestWeb).toHaveBeenCalledWith("https://lattice.dev");
    expect(webInput.value).toBe("");

    fireEvent.click(screen.getByRole("button", { name: /폴더 선택/ }));
    expect(onPickFolder).toHaveBeenCalled();
  });

  it("colors each tile after its pipeline state", () => {
    renderPanel({
      ingestionStates: {
        ...EMPTY_STATES,
        file: ingestion({ stage: "complete", newMemories: 2, newEntities: 1 }),
        folder: ingestion({ sourceType: "folder", stage: "error" }),
        note: ingestion({ sourceType: "note", stage: "embedding" }),
      },
    });
    expect(document.querySelector(".brain-ingest-tile.is-emerged")).toBeTruthy();
    expect(document.querySelector(".brain-ingest-tile.is-failed")).toBeTruthy();
    expect(document.querySelector(".brain-ingest-tile.is-ingesting")).toBeTruthy();
  });
});

describe("IngestionStageTrack stages", () => {
  function renderStage(state: IngestionState | null) {
    return renderPanel({ ingestionStates: { ...EMPTY_STATES, file: state } });
  }

  it("walks the pipeline hints stage by stage", () => {
    const { rerender } = renderStage(ingestion({ stage: "preparing" }));
    expect(screen.getByText("자료를 여는 중")).toBeTruthy();
    expect(document.querySelector(".brain-ingest-stage-badge.is-active")).toBeTruthy();
    expect(document.querySelector(".brain-ingest-spin")).toBeTruthy();

    const handlers = panelHandlers();
    for (const [stage, hint] of [
      ["parsing", "글과 표를 읽어내는 중"],
      ["embedding", "무슨 내용인지 파악하는 중"],
      ["indexing", "이미 아는 것과 이어 붙이는 중"],
    ] as const) {
      rerender(
        <BrainIngestionPanel
          language="ko"
          uploadingDocument={false}
          ingestionStates={{ ...EMPTY_STATES, file: ingestion({ stage }) }}
          {...handlers}
        />,
      );
      expect(screen.getByText(hint)).toBeTruthy();
    }
  });

  it("reports an error without a spinner", () => {
    renderStage(ingestion({ stage: "error" }));
    expect(document.querySelector(".brain-ingest-stage-badge.is-failed")).toBeTruthy();
    expect(document.querySelector(".brain-ingest-spin")).toBeNull();
    expect(document.querySelector(".brain-ingest-stage-hint.is-failed")!.textContent).toBe("실패");
  });

  it("celebrates emergence with counts and inflow motes", () => {
    renderStage(ingestion({ stage: "complete", newMemories: 2, newEntities: 3 }));
    expect(screen.getByText("새 기억 2개 · 새 엔티티 3개")).toBeTruthy();
    expect(document.querySelector(".lattice-inflow")).toBeTruthy();
    expect(document.querySelector(".brain-ingest-stage-badge.is-done")).toBeTruthy();
  });

  it("treats entity-only emergence as emergence too", () => {
    renderStage(ingestion({ stage: "complete", newMemories: 0, newEntities: 4 }));
    expect(screen.getByText("새 기억 0개 · 새 엔티티 4개")).toBeTruthy();
    expect(document.querySelector(".lattice-inflow")).toBeTruthy();
  });

  it("stays honest when a completed ingest merged into existing knowledge", () => {
    renderStage(ingestion({ stage: "complete", newMemories: 0, newEntities: 0 }));
    expect(screen.getByText("기존 지식과 통합됨")).toBeTruthy();
    expect(document.querySelector(".lattice-inflow")).toBeNull();
  });

  it("warns about low extraction quality with up to three reasons", () => {
    renderStage(
      ingestion({
        stage: "complete",
        newMemories: 1,
        extraction: { score: 0.2, level: "low", reasons: [], warnings: ["표 누락", "이미지 스킵", "인코딩 문제", "네번째 경고"] },
      }),
    );
    const note = screen.getByTestId("extraction-quality-note");
    expect(note.textContent).toContain("읽어내기 어려웠어요");
    expect(note.querySelectorAll("li").length).toBe(3);
    expect(note.textContent).not.toContain("네번째 경고");
  });

  it("keeps the low-quality warning terse when the backend gave no details", () => {
    renderStage(
      ingestion({
        stage: "complete",
        newMemories: 1,
        extraction: { score: 0.2, level: "low", reasons: [], warnings: [] },
      }),
    );
    expect(screen.getByTestId("extraction-quality-note").querySelector("ul")).toBeNull();
  });

  it("says nothing about quality when extraction was fine", () => {
    renderStage(
      ingestion({
        stage: "complete",
        newMemories: 1,
        extraction: { score: 0.9, level: "high", reasons: [], warnings: [] },
      }),
    );
    expect(screen.queryByTestId("extraction-quality-note")).toBeNull();
  });
});

function renderDock(
  overrides: Partial<React.ComponentProps<typeof BrainIngestionDock>> = {},
) {
  const handlers = panelHandlers();
  const utils = render(
    <BrainIngestionDock
      language="ko"
      uploadingDocument={false}
      ingestionStates={EMPTY_STATES}
      {...handlers}
      {...overrides}
    />,
  );
  return { ...utils, ...handlers };
}

describe("BrainIngestionDock", () => {
  it("renders the dock header in panel mode and drops it inline", () => {
    const { rerender } = renderDock();
    const dock = screen.getByTestId("brain-ingestion-dock");
    expect(dock.className).not.toContain("is-inline");
    expect(document.querySelector(".brain-ingestion-dock-head")).toBeTruthy();

    const handlers = panelHandlers();
    rerender(
      <BrainIngestionDock
        language="ko"
        uploadingDocument={false}
        ingestionStates={EMPTY_STATES}
        {...handlers}
        variant="inline"
      />,
    );
    expect(screen.getByTestId("brain-ingestion-dock").className).toContain("is-inline");
    expect(document.querySelector(".brain-ingestion-dock-head")).toBeNull();
  });

  it("uploads through the dock file action and ignores empty change events", async () => {
    const { onUploadDocument } = renderDock({ uploadingDocument: false });
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    const file = new File(["x"], "메모.md", { type: "text/markdown" });
    await userEvent.upload(input, file);
    expect(onUploadDocument).toHaveBeenCalledWith(file);

    onUploadDocument.mockClear();
    Object.defineProperty(input, "files", { value: null, configurable: true });
    fireEvent.change(input);
    expect(onUploadDocument).not.toHaveBeenCalled();
  });

  it("marks the file action disabled while uploading and colors action states", () => {
    renderDock({
      uploadingDocument: true,
      ingestionStates: {
        ...EMPTY_STATES,
        file: ingestion({ stage: "complete", newMemories: 1 }),
        folder: ingestion({ sourceType: "folder", stage: "error" }),
        note: ingestion({ sourceType: "note", stage: "indexing" }),
      },
    });
    expect(document.querySelector(".brain-ingestion-dock-action.is-disabled")).toBeTruthy();
    expect(document.querySelector(".brain-ingestion-dock-action.is-emerged")).toBeTruthy();
    expect(document.querySelector(".brain-ingestion-dock-action.is-failed")).toBeTruthy();
    expect(document.querySelector(".brain-ingestion-dock-action.is-ingesting")).toBeTruthy();
  });

  it("toggles a source popover open and closed from the same button", () => {
    renderDock();
    const folderButton = screen.getByRole("button", { name: "폴더" });
    expect(folderButton.getAttribute("aria-expanded")).toBe("false");
    expect(folderButton.getAttribute("aria-controls")).toBeNull();

    fireEvent.click(folderButton);
    expect(folderButton.getAttribute("aria-expanded")).toBe("true");
    const popover = document.querySelector(".brain-ingestion-dock-popover")!;
    expect(folderButton.getAttribute("aria-controls")).toBe(popover.id);
    // The two other source buttons do not claim the popover.
    expect(screen.getByRole("button", { name: "노트" }).getAttribute("aria-controls")).toBeNull();
    expect(screen.getByText("폴더 넣기")).toBeTruthy();

    fireEvent.click(folderButton);
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeNull();
  });

  it("connects a folder from the popover, offering the native picker too", () => {
    const { onConnectFolder, onPickFolder } = renderDock();
    fireEvent.click(screen.getByRole("button", { name: "폴더" }));

    fireEvent.click(screen.getByRole("button", { name: /폴더 선택/ }));
    expect(onPickFolder).toHaveBeenCalled();

    const input = screen.getByLabelText("로컬 폴더 경로");
    fireEvent.change(input, { target: { value: "  " } });
    fireEvent.submit(input.closest("form")!);
    expect(onConnectFolder).not.toHaveBeenCalled();
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeTruthy();

    fireEvent.change(input, { target: { value: "~/자료" } });
    fireEvent.submit(input.closest("form")!);
    expect(onConnectFolder).toHaveBeenCalledWith("~/자료");
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeNull();
  });

  it("ingests a note only when there is real content", () => {
    const { onIngestNote } = renderDock();
    fireEvent.click(screen.getByRole("button", { name: "노트" }));
    const input = document.querySelector<HTMLInputElement>(".brain-ingestion-dock-popover input")!;

    fireEvent.submit(input.closest("form")!);
    expect(onIngestNote).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "회의 메모" } });
    fireEvent.submit(input.closest("form")!);
    expect(onIngestNote).toHaveBeenCalledWith("회의 메모");
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeNull();
  });

  it("ingests a web address only when one was typed", () => {
    const { onIngestWeb } = renderDock();
    fireEvent.click(screen.getByRole("button", { name: "웹" }));
    const input = document.querySelector<HTMLInputElement>(".brain-ingestion-dock-popover input")!;

    fireEvent.submit(input.closest("form")!);
    expect(onIngestWeb).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "https://news.example" } });
    fireEvent.submit(input.closest("form")!);
    expect(onIngestWeb).toHaveBeenCalledWith("https://news.example");
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeNull();
  });

  it("closes the popover on Escape or its close button", () => {
    renderDock();
    fireEvent.click(screen.getByRole("button", { name: "노트" }));
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeTruthy();
    fireEvent.keyDown(screen.getByTestId("brain-ingestion-dock"), { key: "Escape" });
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "웹" }));
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeTruthy();
    fireEvent.keyDown(screen.getByTestId("brain-ingestion-dock"), { key: "Enter" });
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "자료 입력 닫기" }));
    expect(document.querySelector(".brain-ingestion-dock-popover")).toBeNull();
  });

  it("shows the pipeline state for the open source inside the popover", () => {
    renderDock({
      ingestionStates: { ...EMPTY_STATES, web: ingestion({ sourceType: "web", stage: "embedding", label: "https://a" }) },
    });
    fireEvent.click(screen.getByRole("button", { name: "웹" }));
    expect(screen.getByText("무슨 내용인지 파악하는 중")).toBeTruthy();
  });
});

function emergenceEvent(overrides: Partial<EmergenceEvent> = {}): EmergenceEvent {
  return {
    id: "ev-1",
    sourceType: "file",
    label: "보고서.pdf",
    newMemories: 2,
    newEntities: 1,
    nodeIds: [],
    at: Date.now(),
    ...overrides,
  };
}

describe("IngestionTimelineSection", () => {
  it("invites the first ingestion when nothing has emerged yet", () => {
    render(<IngestionTimelineSection language="ko" emergenceEvents={[]} />);
    expect(screen.getByText("아직 수집 기록이 없습니다. 위에서 무엇이든 넣어보세요.")).toBeTruthy();
  });

  it("lists emergence events with source, counts and relative time", () => {
    render(
      <IngestionTimelineSection
        language="ko"
        emergenceEvents={[
          emergenceEvent({ id: "now", at: Date.now() }),
          emergenceEvent({ id: "older", sourceType: "note", label: "메모", newMemories: 5, newEntities: 0, at: Date.now() - 7 * 60_000 }),
        ]}
      />,
    );
    const items = document.querySelectorAll(".brain-emergence-item");
    expect(items.length).toBe(2);
    expect(items[0].textContent).toContain("파일");
    expect(items[0].textContent).toContain("보고서.pdf");
    expect(items[0].textContent).toContain("새 기억 2개");
    expect(items[0].textContent).toContain("새 엔티티 1개");
    expect(items[0].textContent).toContain("방금");
    expect(items[1].textContent).toContain("노트");
    expect(items[1].textContent).toContain("7분 전");
  });
});
