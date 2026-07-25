import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CreatedFilesCard } from "./MessageMarkdown";
import type { MessageFile } from "./types";

function file(overrides: Partial<MessageFile> = {}): MessageFile {
  return { path: "out/page.html", filename: "page.html", bytes: 128, ...overrides };
}

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
});
