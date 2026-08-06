import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { CreatedFilesCard, MessageBody } from "./MessageMarkdown";
import type { MessageFile } from "./types";

function file(overrides: Partial<MessageFile> = {}): MessageFile {
  return { path: "out/page.html", filename: "page.html", bytes: 128, ...overrides };
}

describe("MessageBody markdown rendering", () => {
  it("renders headings, lists, quotes, rules and inline marks without raw markers", () => {
    render(
      <MessageBody
        language="ko"
        content={[
          "# 제목",
          "#### 깊은 제목",
          "",
          "첫 줄 **강조** 와 `코드` 그리고 [링크](https://lattice.dev) 입니다.",
          "둘째 줄",
          "",
          "- 하나",
          "* 둘",
          "• 셋",
          "",
          "1. 첫째",
          "2) 둘째",
          "",
          "> 인용 첫 줄",
          "> 인용 둘째 줄",
          "",
          "---",
          "다음 문단",
        ].join("\n")}
      />,
    );
    const md = document.querySelector(".brain-md")!;
    expect(md.querySelector(".brain-md-heading.is-depth-1")!.textContent).toBe("제목");
    // Depth is clamped at 3 so #### still reads as a heading.
    expect(md.querySelector(".brain-md-heading.is-depth-3")!.textContent).toBe("깊은 제목");
    expect(md.querySelector("strong")!.textContent).toBe("강조");
    expect(md.querySelector("code")!.textContent).toBe("코드");
    const link = md.querySelector("a")!;
    expect(link.getAttribute("href")).toBe("https://lattice.dev");
    expect(link.textContent).toBe("링크");
    expect(md.querySelector("br")).toBeTruthy();
    // The three bullet styles merge into one list; the ordered pair into another.
    expect(md.querySelectorAll("ul").length).toBe(1);
    expect(md.querySelectorAll("ul li").length).toBe(3);
    expect(md.querySelectorAll("ol li").length).toBe(2);
    expect(md.querySelector("blockquote")!.textContent).toBe("인용 첫 줄 인용 둘째 줄");
    // The horizontal rule renders nothing but still separates paragraphs.
    expect(md.textContent).not.toContain("---");
    expect(md.textContent).toContain("다음 문단");
    expect(md.textContent).not.toContain("**");
  });

  it("starts a fresh ordered list after an unordered one and vice versa", () => {
    render(
      <MessageBody
        language="ko"
        content={["- 불릿", "1. 번호", "- 다시 불릿"].join("\n")}
      />,
    );
    const md = document.querySelector(".brain-md")!;
    expect(md.querySelectorAll("ul").length).toBe(2);
    expect(md.querySelectorAll("ol").length).toBe(1);
  });

  it("splits fenced code from prose and keeps unterminated fences visible", () => {
    render(
      <MessageBody
        language="ko"
        content={"앞 문단\n```python\nprint('hi')\n```\n   \n```\ntail without end"}
      />,
    );
    expect(screen.getByText("앞 문단")).toBeTruthy();
    const blocks = document.querySelectorAll(".brain-code-block");
    expect(blocks.length).toBe(2);
    expect(blocks[0].querySelector(".brain-code-lang")!.textContent).toBe("python");
    expect(blocks[0].querySelector("pre code")!.textContent).toBe("print('hi')");
    // A fence without a language falls back to the neutral label.
    expect(blocks[1].querySelector(".brain-code-lang")!.textContent).toBe("text");
    expect(blocks[1].querySelector("pre code")!.textContent).toBe("tail without end");
  });

  it("renders plain content and trailing prose after a fence", () => {
    const { rerender } = render(<MessageBody language="ko" content="그냥 문장" />);
    expect(screen.getByText("그냥 문장")).toBeTruthy();

    rerender(<MessageBody language="ko" content={"```js\n1\n```\n마무리 문장"} />);
    expect(screen.getByText("마무리 문장")).toBeTruthy();

    rerender(<MessageBody language="ko" content="" />);
    expect(document.querySelector(".brain-md")).toBeNull();
    expect(document.querySelector(".brain-code-block")).toBeNull();
  });

  it("renders a line that ends exactly at an inline mark with nothing trailing", () => {
    render(<MessageBody language="ko" content="**끝**" />);
    const md = document.querySelector(".brain-md")!;
    expect(md.querySelector("strong")!.textContent).toBe("끝");
    // Nothing follows the mark, so there is no trailing text node to render.
    expect(md.textContent).toBe("끝");
  });
});

describe("CodeBlock actions", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  function renderCode(content = "```python\nprint('hi')\n```") {
    return render(<MessageBody language="ko" content={content} />);
  }

  it("copies the code and relaxes the label again after two seconds", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderCode();
    await act(async () => {
      fireEvent.click(screen.getByText("복사"));
    });
    expect(writeText).toHaveBeenCalledWith("print('hi')");
    expect(screen.getByText("복사했어요")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(2100);
    });
    expect(screen.getByText("복사")).toBeTruthy();
  });

  it("swallows clipboard failures and copes with no clipboard at all", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.assign(navigator, { clipboard: { writeText } });
    renderCode();
    fireEvent.click(screen.getByText("복사"));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    // Failure keeps the idle label — no false "copied" promise.
    expect(screen.getByText("복사")).toBeTruthy();

    // Without a clipboard API the optional call resolves to nothing and the
    // label still flips (there was no error to report).
    Object.assign(navigator, { clipboard: undefined });
    fireEvent.click(screen.getByText("복사"));
    await screen.findByText("복사했어요");
  });

  it("saves the block as a real file and shows the created-file card", async () => {
    const save = vi.spyOn(latticeApi, "saveChatFile").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { path: "out/chat-code.py", bytes: 11 },
    } as never);
    renderCode();
    fireEvent.click(screen.getByText("파일로 저장"));
    await screen.findByText("chat-code.py");
    expect(save).toHaveBeenCalledWith(expect.stringMatching(/^chat-\d{8}-\d{6}\.py$/), "print('hi')");
    expect(document.querySelector(".brain-created-files.is-compact")).toBeTruthy();
  });

  it("suggests a txt name for unknown or missing languages and survives odd paths", async () => {
    const save = vi.spyOn(latticeApi, "saveChatFile").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { path: "out/" },
    } as never);
    renderCode("```brainfuck\n+++\n```");
    fireEvent.click(screen.getByText("파일로 저장"));
    // A directory-shaped path falls back to the suggested name.
    await screen.findByText(/^chat-\d{8}-\d{6}\.txt$/);
    expect(save).toHaveBeenCalledWith(expect.stringMatching(/\.txt$/), "+++");
  });

  it("suggests a txt name when the fence carries no language token at all", async () => {
    const save = vi.spyOn(latticeApi, "saveChatFile").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { path: "out/plain.txt", bytes: 5 },
    } as never);
    renderCode("```\nplain\n```");
    fireEvent.click(screen.getByText("파일로 저장"));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save).toHaveBeenCalledWith(expect.stringMatching(/^chat-\d{8}-\d{6}\.txt$/), "plain");
  });

  it("names the saved file after its own suggested name when the server omits a path", async () => {
    vi.spyOn(latticeApi, "saveChatFile").mockResolvedValue({
      ok: true, status: 200, source: "live", data: {},
    } as never);
    renderCode();
    fireEvent.click(screen.getByText("파일로 저장"));
    await screen.findByText(/^chat-\d{8}-\d{6}\.py$/);
  });

  it("reports a save failure with the server reason", async () => {
    vi.spyOn(latticeApi, "saveChatFile").mockResolvedValue({
      ok: false, status: 507, source: "live", error: "disk full", data: {},
    } as never);
    renderCode();
    fireEvent.click(screen.getByText("파일로 저장"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("disk full");
  });

  it("falls back to the generic error copy and ignores double-saves", async () => {
    let release: (value: unknown) => void = () => {};
    const save = vi.spyOn(latticeApi, "saveChatFile").mockReturnValue(
      new Promise((resolve) => { release = resolve; }) as never,
    );
    renderCode();
    fireEvent.click(screen.getByText("파일로 저장"));
    await screen.findByText("저장 중…");
    fireEvent.click(screen.getByText("저장 중…"));
    expect(save).toHaveBeenCalledTimes(1);
    release({ ok: false, status: 500, source: "live", data: {} });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("알 수 없는 문제가 생겼어요");
  });
});

describe("CreatedFilesCard brain-ingest chip", () => {
  it("shows the remembered chip when the ingest verdict is ok", () => {
    render(<CreatedFilesCard language="ko" files={[file({ brainIngest: { status: "ok" } })]} />);
    const chip = screen.getByTestId("brain-ingest-chip");
    expect(chip.textContent).toContain("Brain에 기억됨");
    expect(chip.className).toContain("is-ok");
  });

  it("shows the pending chip while indexing is still queued", () => {
    render(<CreatedFilesCard language="ko" files={[file({ brainIngest: { status: "pending" } })]} />);
    const chip = screen.getByTestId("brain-ingest-chip");
    expect(chip.textContent).toContain("기억 대기 중");
    expect(chip.className).toContain("is-pending");
  });

  it("shows the failed chip with the reason as a tooltip", () => {
    render(
      <CreatedFilesCard
        language="ko"
        files={[file({ brainIngest: { status: "failed", detail: "chunker crashed" } })]}
      />,
    );
    const chip = screen.getByTestId("brain-ingest-chip");
    expect(chip.textContent).toContain("기억 실패");
    expect(chip.className).toContain("is-failed");
    expect(chip.getAttribute("title")).toBe("chunker crashed");
  });

  it("renders no chip when the verdict is absent or unknown", () => {
    const { rerender } = render(<CreatedFilesCard language="ko" files={[file()]} />);
    expect(screen.queryByTestId("brain-ingest-chip")).toBeNull();

    rerender(
      <CreatedFilesCard language="ko" files={[file({ brainIngest: { status: "skipped" } })]} />,
    );
    expect(screen.queryByTestId("brain-ingest-chip")).toBeNull();
  });

  it("shows no tooltip for a failure the backend did not explain", () => {
    render(<CreatedFilesCard language="ko" files={[file({ brainIngest: { status: "failed" } })]} />);
    expect(screen.getByTestId("brain-ingest-chip").getAttribute("title")).toBeNull();
  });
});

describe("CreatedFilesCard", () => {
  it("formats sizes, marks repairs, and offers preview only for previewable files", () => {
    render(
      <CreatedFilesCard
        language="ko"
        files={[
          file({ path: "a.txt", filename: "a.txt", bytes: 500 }),
          file({ path: "b.md", filename: "b.md", bytes: 2048, repaired: true }),
          file({ path: "c.zip", filename: "c.zip", bytes: 3 * 1024 * 1024 }),
          file({ path: "d.bin", filename: "d.bin", bytes: 0 }),
        ]}
      />,
    );
    expect(screen.getByText("500 B")).toBeTruthy();
    expect(screen.getByText("2.0 KB")).toBeTruthy();
    expect(screen.getByText("3.0 MB")).toBeTruthy();
    expect(screen.getByText(/자동 보정됨/)).toBeTruthy();
    // txt/md preview; zip and the empty bin do not.
    expect(screen.getAllByRole("button", { name: /미리보기/ }).length).toBe(2);
    expect(screen.getAllByRole("button", { name: /다운로드/ }).length).toBe(4);
    expect(document.querySelector(".brain-created-files.is-compact")).toBeNull();
  });

  it("opens and closes the preview modal from the file card", async () => {
    vi.spyOn(latticeApi, "readWorkspaceFile").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { content: "본문" },
    } as never);
    render(<CreatedFilesCard language="ko" files={[file({ path: "a.txt", filename: "a.txt" })]} />);
    fireEvent.click(screen.getByRole("button", { name: /미리보기/ }));
    await screen.findByTestId("file-preview-modal");
    fireEvent.click(screen.getByRole("button", { name: "미리보기 닫기" }));
    expect(screen.queryByTestId("file-preview-modal")).toBeNull();
  });

  it("downloads one file at a time and clears the error on the next try", async () => {
    let release: (value: unknown) => void = () => {};
    const download = vi.spyOn(latticeApi, "downloadWorkspaceFile").mockReturnValue(
      new Promise((resolve) => { release = resolve; }) as never,
    );
    render(
      <CreatedFilesCard
        language="ko"
        files={[file({ path: "a.txt", filename: "a.txt" }), file({ path: "b.txt", filename: "b.txt" })]}
      />,
    );
    const buttons = screen.getAllByRole("button", { name: /다운로드/ });
    fireEvent.click(buttons[0]);
    await screen.findByText("내려받는 중…");
    // While one download runs, a second request is ignored entirely.
    fireEvent.click(buttons[1]);
    expect(download).toHaveBeenCalledTimes(1);
    expect(download).toHaveBeenCalledWith("a.txt", "a.txt");
    release({ ok: true });
    await waitFor(() => expect(screen.queryByText("내려받는 중…")).toBeNull());
  });

  it("surfaces a download failure with the reason or the generic copy", async () => {
    const download = vi.spyOn(latticeApi, "downloadWorkspaceFile").mockResolvedValue({
      ok: false, error: "네트워크 끊김",
    } as never);
    render(<CreatedFilesCard language="ko" files={[file({ path: "a.txt", filename: "a.txt" })]} />);
    fireEvent.click(screen.getByRole("button", { name: /다운로드/ }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("네트워크 끊김");

    download.mockResolvedValue({ ok: false } as never);
    fireEvent.click(screen.getByRole("button", { name: /다운로드/ }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("알 수 없는 문제가 생겼어요"),
    );
  });
});
