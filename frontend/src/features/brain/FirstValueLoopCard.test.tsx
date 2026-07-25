import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { FirstValueLoopCard } from "./FirstValueLoopCard";
import { markFirstValueLoopAsked, markFirstValueLoopFileGenTried, readFirstValueLoopState } from "./firstValueLoop";

const DOCS = [
  { demo_id: "meeting-note", title: "주간 회의록 — 사이드 프로젝트 킥오프", source_uri: "demo://meeting-note", status: "ok" },
  { demo_id: "project-doc", title: "프로젝트 개요 — 새싹 가든", source_uri: "demo://project-doc", status: "ok" },
  { demo_id: "personal-note", title: "개인 노트 — 독서 메모", source_uri: "demo://personal-note", status: "ok" },
];

const QUESTIONS = [
  { question: "회의에서 결정한 출시일이 언제야?", expected_source_uri: "demo://meeting-note", expected_title: "주간 회의록" },
  { question: "새싹 가든의 기술 스택이 뭐야?", expected_source_uri: "demo://project-doc", expected_title: "프로젝트 개요" },
];

function mockStatus(data: Partial<{ installed: boolean; documents: unknown[]; suggested_questions: unknown[] }>) {
  return vi.spyOn(latticeApi, "demoCorpusStatus").mockResolvedValue({
    ok: true, status: 200, source: "live",
    data: { installed: false, documents: [], document_count: 0, suggested_questions: [], ...data },
  } as never);
}

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("FirstValueLoopCard", () => {
  it("injects the demo corpus and then renders docs + question chips", async () => {
    mockStatus({ installed: false });
    const install = vi.spyOn(latticeApi, "installDemoCorpus").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { status: "ok", ingested: 3, duplicates: 0, documents: DOCS, suggested_questions: QUESTIONS },
    } as never);
    const onSendText = vi.fn();

    renderWithQuery(<FirstValueLoopCard language="ko" streaming={false} onSendText={onSendText} />);
    await userEvent.click(await screen.findByTestId("fvl-start"));

    await waitFor(() => expect(install).toHaveBeenCalledTimes(1));
    expect((await screen.findAllByTestId("fvl-doc")).length).toBe(3);
    const chips = screen.getAllByTestId("fvl-chip");
    expect(chips.length).toBe(2);
    expect(chips[0].textContent).toContain("출시일");

    // Clicking a chip sends the question through the normal chat ask path and
    // persists the "asked" progress so the next step can appear.
    await userEvent.click(chips[0]);
    expect(onSendText).toHaveBeenCalledWith("회의에서 결정한 출시일이 언제야?");
    expect(readFirstValueLoopState().asked).toBe(true);
    expect(screen.getByTestId("fvl-filegen-chip")).toBeTruthy();
  });

  it("shows the question chips instead of the inject button when already installed", async () => {
    mockStatus({ installed: true, documents: DOCS, suggested_questions: QUESTIONS });
    renderWithQuery(<FirstValueLoopCard language="ko" streaming={false} onSendText={() => {}} />);

    expect((await screen.findAllByTestId("fvl-chip")).length).toBe(2);
    expect(screen.queryByTestId("fvl-start")).toBeNull();
  });

  it("sends the file-generation prompt from the next-step chip", async () => {
    mockStatus({ installed: true, documents: DOCS, suggested_questions: QUESTIONS });
    markFirstValueLoopAsked();
    const onSendText = vi.fn();
    renderWithQuery(<FirstValueLoopCard language="ko" streaming={false} onSendText={onSendText} />);

    await userEvent.click(await screen.findByTestId("fvl-filegen-chip"));
    expect(onSendText).toHaveBeenCalledTimes(1);
    expect(String(onSendText.mock.calls[0][0])).toContain("HTML");
    expect(readFirstValueLoopState().fileGenTried).toBe(true);
  });

  it("removes the demo data and retires the track", async () => {
    mockStatus({ installed: true, documents: DOCS, suggested_questions: QUESTIONS });
    const remove = vi.spyOn(latticeApi, "removeDemoCorpus").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { status: "ok", removed_count: 3, removed: [] },
    } as never);
    renderWithQuery(<FirstValueLoopCard language="ko" streaming={false} onSendText={() => {}} />);

    await userEvent.click(await screen.findByTestId("fvl-remove"));
    await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId("first-value-loop")).toBeNull());
    expect(readFirstValueLoopState().dismissed).toBe(true);
  });

  it("never re-prompts once dismissed", async () => {
    mockStatus({ installed: false });
    localStorage.setItem("lattice.firstValueLoop.dismissed", "true");
    renderWithQuery(<FirstValueLoopCard language="ko" streaming={false} onSendText={() => {}} />);
    await Promise.resolve();
    expect(screen.queryByTestId("first-value-loop")).toBeNull();
  });

  it("stays silent when the demo corpus endpoint is unavailable", async () => {
    vi.spyOn(latticeApi, "demoCorpusStatus").mockResolvedValue({
      ok: false, status: 503, source: "unavailable",
      data: { installed: false, documents: [], document_count: 0, suggested_questions: [] },
      error: "Knowledge Graph ingestion is disabled.",
    } as never);
    renderWithQuery(<FirstValueLoopCard language="ko" streaming={false} onSendText={() => {}} />);
    await Promise.resolve();
    expect(screen.queryByTestId("first-value-loop")).toBeNull();
  });

  it("ends the done state in real next steps: connect a folder or clean up", async () => {
    mockStatus({ installed: true, documents: DOCS, suggested_questions: QUESTIONS });
    markFirstValueLoopAsked();
    markFirstValueLoopFileGenTried();
    const remove = vi.spyOn(latticeApi, "removeDemoCorpus").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { status: "ok", removed_count: 3, removed: [] },
    } as never);
    const onConnectData = vi.fn();
    renderWithQuery(
      <FirstValueLoopCard
        language="ko"
        streaming={false}
        onSendText={() => {}}
        onConnectData={onConnectData}
      />,
    );

    // The connect CTA hands off to the real ingestion dock.
    await userEvent.click(await screen.findByTestId("fvl-connect-cta"));
    expect(onConnectData).toHaveBeenCalledTimes(1);

    // The standalone remove button collapsed into the cleanup CTA.
    expect(screen.queryByTestId("fvl-remove")).toBeNull();
    await userEvent.click(screen.getByTestId("fvl-cleanup-cta"));
    await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId("first-value-loop")).toBeNull());
  });
});
