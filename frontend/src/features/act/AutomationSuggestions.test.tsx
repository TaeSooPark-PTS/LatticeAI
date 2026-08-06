import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { ok, renderPage } from "@/test/renderPage";
import { AutomationSuggestions } from "./AutomationSuggestions";

/**
 * The consent-first suggestion strip: what the Brain noticed, why, and an
 * install that only ever creates a disabled draft. The card copy must localise
 * per suggestion kind, and an already-installed suggestion must not offer a
 * second install.
 */

const SUGGESTIONS = {
  suggestions: [
    { id: "s-question", kind: "question", title: "오늘 뭐 했지?", cadence: "daily", installed: false, reason: { count: 4 } },
    { id: "s-folder", kind: "knowledge_source", title: "~/Docs", cadence: "", installed: true, reason: { indexed_files: 12 } },
    { id: "s-bare-folder", kind: "knowledge_source", title: "폴더", installed: false },
    { id: "s-bare-question", kind: "question", title: "질문", installed: false, reason: {} },
  ],
  questions_scanned: 7,
};

describe("AutomationSuggestions", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows the empty state, the note, and no scan count when nothing was scanned", async () => {
    renderPage(<AutomationSuggestions language="ko" />, {
      api: { automationOverview: ok({ suggestions: [], questions_scanned: 0 }) },
    });
    await screen.findByText("아직 제안이 없습니다. 질문을 하고 폴더를 연결할수록 제안이 생깁니다.");
    expect(screen.getByText(/제안을 수락하면 꺼진 초안으로/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("분석");
  });

  it("renders each suggestion kind with its evidence, cadence and scan count", async () => {
    renderPage(<AutomationSuggestions language="ko" />, {
      api: { automationOverview: ok(SUGGESTIONS) },
    });
    await screen.findByText("오늘 뭐 했지?");
    // The subtitle carries how many questions were scanned.
    expect(screen.getByText(/최근 질문 7개 분석/)).toBeTruthy();
    // Question kind: repeat count evidence and a daily cadence.
    expect(screen.getByText("4번 반복해서 물어보셨어요")).toBeTruthy();
    expect(screen.getAllByText("매일 자동 초안").length).toBeGreaterThan(0);
    // Folder kind: file-count evidence and the new-knowledge cadence.
    expect(screen.getByText("파일 12개가 Brain에 담긴 폴더예요")).toBeTruthy();
    expect(screen.getAllByText("새 지식이 들어올 때").length).toBeGreaterThan(0);
    // Missing reason payloads count as zero rather than exploding.
    expect(screen.getByText("파일 0개가 Brain에 담긴 폴더예요")).toBeTruthy();
    expect(screen.getByText("0번 반복해서 물어보셨어요")).toBeTruthy();
    // The installed suggestion shows the draft badge instead of a button.
    expect(screen.getByText("초안 생성됨 — 검토 후 켜세요")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "자동화 만들기" })).toHaveLength(3);
  });

  it("installs a suggestion as a disabled draft and refreshes both panels", async () => {
    renderPage(<AutomationSuggestions language="ko" />, {
      api: { automationOverview: ok(SUGGESTIONS) },
    });
    await screen.findByText("오늘 뭐 했지?");
    await userEvent.click(screen.getAllByRole("button", { name: "자동화 만들기" })[0]);
    await waitFor(() =>
      expect(latticeApi.installAutomationSuggestion).toHaveBeenCalledWith("s-question", false),
    );
  });

  it("names only the clicked card as installing while the install runs", async () => {
    let resolve!: (value: unknown) => void;
    renderPage(<AutomationSuggestions language="ko" />, {
      api: {
        automationOverview: ok(SUGGESTIONS),
        installAutomationSuggestion: () => new Promise((res) => { resolve = res; }),
      },
    });
    await screen.findByText("오늘 뭐 했지?");
    await userEvent.click(screen.getAllByRole("button", { name: "자동화 만들기" })[0]);
    await screen.findByRole("button", { name: "만드는 중..." });
    // The other cards keep their label but are disabled during the flight.
    const others = screen.getAllByRole("button", { name: "자동화 만들기" });
    expect(others).toHaveLength(2);
    expect(others.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
    resolve(ok({}));
    await waitFor(() => expect(screen.queryByRole("button", { name: "만드는 중..." })).toBeNull());
  });

  it("reads in English when asked to", async () => {
    renderPage(<AutomationSuggestions language="en" />, {
      api: { automationOverview: ok(SUGGESTIONS) },
      language: "en",
    });
    await screen.findByText("Automation suggestions for you");
    expect(document.body.textContent).not.toMatch(/자동화 만들기|반복 질문/);
  });
});
