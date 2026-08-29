import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { BrainComposer, fileToDataUrl } from "./BrainComposer";

// Detached-ref harness: with `detachedRefs.on`, `useRef` still registers its
// real hook slot (hook order stays untouched) but hands the component a facade
// whose reads are null and writes are dropped — the state a ref is in while
// its node is detached. This is how the composer's "ref already gone" guards
// (the height measurer and the scheduled hover-close) are made observable.
const detachedRefs = vi.hoisted(() => ({ on: false }));
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  const useRef = ((initial: unknown) => {
    const real = actual.useRef(initial);
    if (!detachedRefs.on) return real;
    return {
      get current() {
        return null;
      },
      set current(_value) {
        // A detached ref swallows writes.
      },
    };
  }) as typeof actual.useRef;
  return { ...actual, useRef };
});

function renderComposer(overrides: Partial<React.ComponentProps<typeof BrainComposer>> = {}) {
  const props = {
    language: "ko" as const,
    draft: "",
    streaming: false,
    imageData: null,
    uploadingDocument: false,
    onDraftChange: vi.fn(),
    onImageDataChange: vi.fn(),
    onUploadDocument: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    ...overrides,
  };
  const view = render(<BrainComposer {...props} />);
  return { ...view, props };
}

const toggle = () => screen.getByTestId("brain-attach-toggle");
const menu = () => screen.queryByTestId("brain-attach-menu");

afterEach(() => {
  detachedRefs.on = false;
  vi.useRealTimers();
});

describe("BrainComposer typing and sending", () => {
  it("offers tool starters that fill the draft", () => {
    const { props } = renderComposer();
    const search = screen.getByRole("button", { name: t("ko", "brain.composer.can.search") });
    fireEvent.click(search);
    expect(props.onDraftChange).toHaveBeenCalledWith(t("ko", "brain.composer.can.search.prompt"));
  });

  it("sends on Enter, never mid-composition or with Shift", () => {
    const { props } = renderComposer({ draft: "질문" });
    const textarea = screen.getByPlaceholderText(t("ko", "brain.placeholder"));

    fireEvent.keyDown(textarea, { key: "Enter", isComposing: true });
    expect(props.onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(props.onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "a" });
    expect(props.onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(props.onSend).toHaveBeenCalledTimes(1);
  });

  it("reports draft changes and re-measures its height as the draft grows", () => {
    const { props, rerender } = renderComposer();
    const textarea = screen.getByPlaceholderText(t("ko", "brain.placeholder")) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "한 줄" } });
    expect(props.onDraftChange).toHaveBeenCalledWith("한 줄");

    rerender(
      <BrainComposer
        {...props}
        draft={"첫 줄\n둘째 줄\n셋째 줄"}
      />,
    );
    // jsdom reports scrollHeight 0; the effect still must have written a
    // measured pixel height rather than leaving "auto".
    expect(textarea.style.height).toMatch(/px$/);
  });

  it("disables Send without a draft and sends with one", () => {
    const { props, rerender } = renderComposer();
    const send = screen.getByRole("button", { name: new RegExp(t("ko", "brain.send")) });
    expect(send).toBeDisabled();

    rerender(<BrainComposer {...props} draft="질문 있어요" />);
    expect(send).toBeEnabled();
    fireEvent.click(send);
    expect(props.onSend).toHaveBeenCalledTimes(1);
  });

  it("swaps Send for Stop while streaming, and keeps Send disabled when no stop handler exists", () => {
    const { props, rerender, container } = renderComposer({ draft: "질문", streaming: true });
    expect(container.querySelector(".brain-composer")).toHaveAttribute("aria-busy", "true");
    const stop = screen.getByRole("button", { name: new RegExp(t("ko", "brain.stop")) });
    fireEvent.click(stop);
    expect(props.onStop).toHaveBeenCalledTimes(1);

    rerender(<BrainComposer {...props} onStop={undefined} />);
    expect(screen.queryByRole("button", { name: new RegExp(t("ko", "brain.stop")) })).toBeNull();
    expect(screen.getByRole("button", { name: new RegExp(t("ko", "brain.send")) })).toBeDisabled();
  });

  it("shows the attached-image confirmation", () => {
    renderComposer({ imageData: "data:image/png;base64,aaaa" });
    expect(screen.getByText(t("ko", "brain.imageAttached"))).toBeTruthy();
  });
});

describe("BrainComposer attach menu", () => {
  it("opens on click and refuses to close on the click that follows a fresh open", () => {
    vi.useFakeTimers();
    renderComposer();
    expect(menu()).toBeNull();
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(toggle()).not.toHaveAttribute("aria-controls");

    fireEvent.click(toggle());
    expect(menu()).toBeTruthy();
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(toggle()).toHaveAttribute("aria-controls", menu()!.id);

    // A click landing within the 350ms debounce is the click that opened it
    // (or the hover-open race) — it must not read as "toggle closed".
    act(() => {
      vi.advanceTimersByTime(100);
    });
    fireEvent.click(toggle());
    expect(menu()).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(300);
    });
    fireEvent.click(toggle());
    expect(menu()).toBeNull();
  });

  it("opens on mouse hover, ignores touch, and closes after the pointer leaves", () => {
    vi.useFakeTimers();
    renderComposer();
    const attach = toggle().parentElement as HTMLElement;

    fireEvent.pointerOver(attach, { pointerType: "touch" });
    expect(menu()).toBeNull();

    fireEvent.pointerOver(attach, { pointerType: "mouse" });
    expect(menu()).toBeTruthy();

    // Re-entering while open must keep it open (openedAt only set once).
    fireEvent.pointerOver(attach, { pointerType: "mouse" });
    expect(menu()).toBeTruthy();

    fireEvent.pointerOut(attach, { pointerType: "touch" });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(menu()).toBeTruthy();

    fireEvent.pointerOut(attach, { pointerType: "mouse" });
    expect(menu()).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(240);
    });
    expect(menu()).toBeNull();
  });

  it("cancels a scheduled hover-close when the pointer returns", () => {
    vi.useFakeTimers();
    renderComposer();
    const attach = toggle().parentElement as HTMLElement;
    fireEvent.pointerOver(attach, { pointerType: "mouse" });
    fireEvent.pointerOut(attach, { pointerType: "mouse" });
    fireEvent.pointerOver(attach, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(menu()).toBeTruthy();
  });

  it("keeps the menu open while something inside it has focus", () => {
    vi.useFakeTimers();
    renderComposer();
    const attach = toggle().parentElement as HTMLElement;
    fireEvent.pointerOver(attach, { pointerType: "mouse" });
    toggle().focus();

    fireEvent.pointerOut(attach, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(240);
    });
    expect(menu()).toBeTruthy();
  });

  it("keeps the menu open while a capture popover is showing", () => {
    vi.useFakeTimers();
    renderComposer({ attachments: <div className="brain-ingestion-dock-popover">폴더 입력</div> });
    const attach = toggle().parentElement as HTMLElement;
    fireEvent.pointerOver(attach, { pointerType: "mouse" });
    (document.activeElement as HTMLElement | null)?.blur?.();

    fireEvent.pointerOut(attach, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(240);
    });
    expect(menu()).toBeTruthy();
  });

  it("keeps the menu open while a document upload is in flight", () => {
    vi.useFakeTimers();
    renderComposer({ uploadingDocument: true });
    const attach = toggle().parentElement as HTMLElement;
    fireEvent.pointerOver(attach, { pointerType: "mouse" });

    fireEvent.pointerOut(attach, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(240);
    });
    expect(menu()).toBeTruthy();
    expect(screen.getByText(t("ko", "brain.upload.uploading"))).toBeTruthy();
  });

  it("closes on Escape only when no capture popover is open, and ignores other keys", () => {
    renderComposer();
    // Escape with the menu closed is a no-op.
    fireEvent.keyDown(toggle(), { key: "Escape" });
    expect(menu()).toBeNull();

    fireEvent.click(toggle());
    fireEvent.keyDown(toggle(), { key: "a" });
    expect(menu()).toBeTruthy();
    fireEvent.keyDown(toggle(), { key: "Escape" });
    expect(menu()).toBeNull();
  });

  it("hands Escape to an open capture popover instead of closing the menu", () => {
    renderComposer({ attachments: <div className="brain-ingestion-dock-popover">웹 주소</div> });
    fireEvent.click(toggle());
    fireEvent.keyDown(toggle(), { key: "Escape" });
    expect(menu()).toBeTruthy();
  });

  it("dismisses on pointerdown outside but not inside", () => {
    renderComposer();
    fireEvent.click(toggle());
    fireEvent.pointerDown(toggle());
    expect(menu()).toBeTruthy();

    fireEvent.pointerDown(document.body);
    expect(menu()).toBeNull();
  });

  it("dismisses even for a synthetic pointerdown that carries no target", () => {
    renderComposer();
    fireEvent.click(toggle());
    const event = new Event("pointerdown", { bubbles: true });
    Object.defineProperty(event, "target", { value: null });
    fireEvent(document.body, event);
    expect(menu()).toBeNull();
  });

  it("survives its refs detaching: measuring and the scheduled close become no-ops", () => {
    vi.useFakeTimers();
    detachedRefs.on = true;
    renderComposer({ draft: "이미 적힌 질문" });
    // The height measurer ran against a detached ref and had to bail — the
    // textarea keeps whatever height the browser gave it.
    const textarea = screen.getByPlaceholderText(t("ko", "brain.placeholder")) as HTMLTextAreaElement;
    expect(textarea.style.height).toBe("");

    // A hover-close that fires after the ref is gone must leave the menu be.
    fireEvent.click(toggle());
    const attach = toggle().parentElement as HTMLElement;
    fireEvent.pointerOut(attach, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(240);
    });
    expect(menu()).toBeTruthy();
  });
});

describe("BrainComposer file inputs", () => {
  function imageInput(container: HTMLElement) {
    return container.querySelector(".brain-image-input input[type=file]") as HTMLInputElement;
  }
  function documentInput(container: HTMLElement) {
    return container.querySelector(".brain-document-input input[type=file]") as HTMLInputElement;
  }

  it("turns a picked image into a data URL", async () => {
    const { container, props } = renderComposer();
    fireEvent.click(toggle());
    const file = new File(["fake-bytes"], "photo.png", { type: "image/png" });
    fireEvent.change(imageInput(container), { target: { files: [file] } });
    await waitFor(() => expect(props.onImageDataChange).toHaveBeenCalledTimes(1));
    expect(String(vi.mocked(props.onImageDataChange).mock.calls[0][0])).toMatch(/^data:/);
  });

  it("ignores an image change with no file selected", () => {
    const { container, props } = renderComposer();
    fireEvent.click(toggle());
    fireEvent.change(imageInput(container), { target: { files: [] } });
    const input = imageInput(container);
    Object.defineProperty(input, "files", { value: null, configurable: true });
    fireEvent.change(input);
    expect(props.onImageDataChange).not.toHaveBeenCalled();
  });

  it("forwards a picked document and shows the idle CTA when not uploading", () => {
    const { container, props } = renderComposer();
    fireEvent.click(toggle());
    expect(screen.getByText(t("ko", "brain.upload.ctaShort"))).toBeTruthy();
    expect(documentInput(container)).toBeEnabled();

    const file = new File(["문서"], "notes.md", { type: "text/markdown" });
    fireEvent.change(documentInput(container), { target: { files: [file] } });
    expect(props.onUploadDocument).toHaveBeenCalledWith(file);

    fireEvent.change(documentInput(container), { target: { files: [] } });
    expect(props.onUploadDocument).toHaveBeenCalledTimes(1);
  });

  it("disables the document input while uploading", () => {
    const { container } = renderComposer({ uploadingDocument: true });
    fireEvent.click(toggle());
    expect(documentInput(container)).toBeDisabled();
    expect(container.querySelector(".brain-document-input")).toHaveClass("is-disabled");
  });
});

describe("fileToDataUrl", () => {
  it("resolves with the reader result", async () => {
    await expect(fileToDataUrl(new File(["x"], "x.txt", { type: "text/plain" }))).resolves.toMatch(/^data:/);
  });

  it("rejects when the reader errors", async () => {
    const boom = new Error("read failed");
    class FailingReader {
      onload: null | (() => void) = null;
      onerror: null | (() => void) = null;
      error = boom;
      result = null;
      readAsDataURL() {
        queueMicrotask(() => this.onerror?.());
      }
    }
    vi.stubGlobal("FileReader", FailingReader);
    try {
      await expect(fileToDataUrl(new File(["x"], "x.txt"))).rejects.toBe(boom);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("resolves to an empty string when the reader reports no result", async () => {
    class EmptyReader {
      onload: null | (() => void) = null;
      onerror: null | (() => void) = null;
      error = null;
      result: string | null = null;
      readAsDataURL() {
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal("FileReader", EmptyReader);
    try {
      await expect(fileToDataUrl(new File(["x"], "x.txt"))).resolves.toBe("");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
