import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { FilePreviewModal, htmlWithPreviewCsp, isPreviewableFile, prettyJson, previewKind } from "./FilePreviewModal";

function mockRead(content: string) {
  return vi.spyOn(latticeApi, "readWorkspaceFile").mockResolvedValue({
    ok: true, status: 200, source: "live", data: { content },
  } as never);
}

describe("preview helpers", () => {
  it("classifies preview kinds by extension", () => {
    expect(previewKind("page.html")).toBe("html");
    expect(previewKind("notes.md")).toBe("markdown");
    expect(previewKind("data.json")).toBe("json");
    expect(previewKind("log.txt")).toBe("text");
  });

  it("prefers the backend previewable flag and falls back to extensions", () => {
    expect(isPreviewableFile({ path: "a.bin", filename: "a.bin", bytes: 1, previewable: true })).toBe(true);
    expect(isPreviewableFile({ path: "a.html", filename: "a.html", bytes: 1, previewable: false })).toBe(false);
    expect(isPreviewableFile({ path: "a.md", filename: "a.md", bytes: 1 })).toBe(true);
    expect(isPreviewableFile({ path: "a.zip", filename: "a.zip", bytes: 1 })).toBe(false);
  });

  it("injects a restrictive CSP into HTML previews", () => {
    expect(htmlWithPreviewCsp("<p>hi</p>")).toContain("Content-Security-Policy");
    const withHead = htmlWithPreviewCsp("<html><head><title>x</title></head><body></body></html>");
    expect(withHead.indexOf("Content-Security-Policy")).toBeGreaterThan(withHead.indexOf("<head>"));
    expect(withHead.indexOf("Content-Security-Policy")).toBeLessThan(withHead.indexOf("<title>"));
  });

  it("pretty-prints valid JSON and passes through broken JSON", () => {
    expect(prettyJson('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(prettyJson("not json")).toBe("not json");
  });
});

describe("FilePreviewModal", () => {
  it("renders pretty-printed JSON in the modal", async () => {
    mockRead('{"name":"Lattice","count":2}');
    render(
      <FilePreviewModal
        language="ko"
        file={{ path: "out/data.json", filename: "data.json", bytes: 30 }}
        onClose={() => {}}
      />,
    );
    const code = await screen.findByTestId("file-preview-code");
    expect(code.textContent).toContain('"name": "Lattice"');
    expect(code.textContent).toContain('"count": 2');
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("renders markdown files with the shared markdown renderer", async () => {
    mockRead("# Title\n\n- first item\n- **bold** item");
    render(
      <FilePreviewModal
        language="ko"
        file={{ path: "out/notes.md", filename: "notes.md", bytes: 20 }}
        onClose={() => {}}
      />,
    );
    const body = await screen.findByTestId("file-preview-markdown");
    expect(body.textContent).toContain("Title");
    expect(body.querySelectorAll("li").length).toBe(2);
    expect(body.querySelector("strong")?.textContent).toBe("bold");
  });

  it("shows an error state when the file cannot be read", async () => {
    vi.spyOn(latticeApi, "readWorkspaceFile").mockResolvedValue({
      ok: false, status: 404, source: "unavailable", data: { content: "" }, error: "missing",
    } as never);
    render(
      <FilePreviewModal
        language="ko"
        file={{ path: "out/gone.txt", filename: "gone.txt", bytes: 0 }}
        onClose={() => {}}
      />,
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("missing");
  });

  it("traps focus inside the dialog and closes on Escape", async () => {
    mockRead("plain text");
    const onClose = vi.fn();
    render(
      <FilePreviewModal
        language="ko"
        file={{ path: "out/a.txt", filename: "a.txt", bytes: 5 }}
        onClose={onClose}
      />,
    );
    await screen.findByTestId("file-preview-code");
    const dialog = screen.getByRole("dialog");
    const buttons = dialog.querySelectorAll("button");
    expect(buttons.length).toBe(2);

    // Forward Tab from the last focusable wraps back to the first.
    (buttons[buttons.length - 1] as HTMLElement).focus();
    await userEvent.tab();
    expect(document.activeElement).toBe(buttons[0]);
    // Shift+Tab from the first wraps to the last.
    await userEvent.tab({ shift: true });
    expect(document.activeElement).toBe(buttons[buttons.length - 1]);

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
