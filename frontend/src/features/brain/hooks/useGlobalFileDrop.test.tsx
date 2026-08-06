import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useGlobalFileDrop } from "./useGlobalFileDrop";

/**
 * Full-viewport drag-and-drop capture. jsdom has no native DragEvent, so each
 * test dispatches a plain Event carrying the `dataTransfer` shape the handler
 * reads — which is also what lets the non-file and no-dataTransfer branches be
 * driven precisely.
 */

type FakeTransfer = { types?: string[]; files?: File[]; dropEffect?: string };

function dragEvent(type: string, dataTransfer?: FakeTransfer | null) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  if (dataTransfer !== undefined) {
    Object.defineProperty(event, "dataTransfer", { value: dataTransfer });
  }
  return event;
}

function dispatch(event: Event) {
  act(() => {
    window.dispatchEvent(event);
  });
  return event;
}

const filesTransfer = (files?: File[]): FakeTransfer => ({ types: ["Files"], ...(files ? { files } : {}), dropEffect: "" });

describe("useGlobalFileDrop", () => {
  it("lights up while a file drag is over the window and tracks nested enters", () => {
    const { result } = renderHook(() => useGlobalFileDrop(vi.fn()));
    expect(result.current).toBe(false);

    dispatch(dragEvent("dragenter", filesTransfer()));
    expect(result.current).toBe(true);

    // Entering a child fires another dragenter; leaving it must not clear.
    dispatch(dragEvent("dragenter", filesTransfer()));
    dispatch(dragEvent("dragleave", filesTransfer()));
    expect(result.current).toBe(true);

    dispatch(dragEvent("dragleave", filesTransfer()));
    expect(result.current).toBe(false);
  });

  it("ignores drags that carry no files", () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => useGlobalFileDrop(onFiles));

    dispatch(dragEvent("dragenter", { types: ["text/plain"] }));
    expect(result.current).toBe(false);

    // No dataTransfer at all (jsdom default, or a synthetic drag).
    dispatch(dragEvent("dragenter"));
    expect(result.current).toBe(false);

    const over = dispatch(dragEvent("dragover", { types: ["text/uri-list"] }));
    expect(over.defaultPrevented).toBe(false);

    dispatch(dragEvent("dragleave", { types: ["text/plain"] }));
    expect(result.current).toBe(false);
    expect(onFiles).not.toHaveBeenCalled();
  });

  it("dragover blocks navigation and marks the copy effect", () => {
    renderHook(() => useGlobalFileDrop(vi.fn()));
    const transfer = filesTransfer();
    const event = dispatch(dragEvent("dragover", transfer));
    expect(event.defaultPrevented).toBe(true);
    expect(transfer.dropEffect).toBe("copy");
  });

  it("survives a dataTransfer the DOM types allow to be null on a later read", () => {
    // TS types every `event.dataTransfer` read as nullable, and the type-check
    // helper cannot narrow the handler's later reads — so the handler re-guards.
    // Drive that defensive branch with a transfer that vanishes after the check.
    renderHook(() => useGlobalFileDrop(vi.fn()));
    const reads: Array<FakeTransfer | null> = [filesTransfer(), null];
    const event = new Event("dragover", { cancelable: true });
    Object.defineProperty(event, "dataTransfer", { get: () => reads.shift() ?? null });
    dispatch(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it("hands dropped files to the callback and clears the overlay", () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => useGlobalFileDrop(onFiles));
    const noteFile = new File(["메모"], "메모.txt", { type: "text/plain" });
    const imageFile = new File(["img"], "사진.png", { type: "image/png" });

    dispatch(dragEvent("dragenter", filesTransfer()));
    expect(result.current).toBe(true);

    const drop = dispatch(dragEvent("drop", filesTransfer([noteFile, imageFile])));
    expect(drop.defaultPrevented).toBe(true);
    expect(onFiles).toHaveBeenCalledWith([noteFile, imageFile]);
    expect(result.current).toBe(false);
  });

  it("a file drop without a file list still clears without calling back", () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => useGlobalFileDrop(onFiles));
    dispatch(dragEvent("dragenter", filesTransfer()));

    dispatch(dragEvent("drop", filesTransfer()));
    expect(onFiles).not.toHaveBeenCalled();
    expect(result.current).toBe(false);
  });

  it("a non-file drop clears the overlay and hands nothing over", () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => useGlobalFileDrop(onFiles));
    dispatch(dragEvent("dragenter", filesTransfer()));
    expect(result.current).toBe(true);

    dispatch(dragEvent("drop", { types: ["text/plain"] }));
    expect(result.current).toBe(false);
    expect(onFiles).not.toHaveBeenCalled();
  });

  it("a cancelled drag (dragend) resets the overlay", () => {
    const { result } = renderHook(() => useGlobalFileDrop(vi.fn()));
    dispatch(dragEvent("dragenter", filesTransfer()));
    expect(result.current).toBe(true);

    dispatch(dragEvent("dragend"));
    expect(result.current).toBe(false);
  });

  it("always drops into the latest callback, and unmounting detaches everything", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender, unmount } = renderHook(({ onFiles }) => useGlobalFileDrop(onFiles), {
      initialProps: { onFiles: first },
    });
    rerender({ onFiles: second });

    const file = new File(["x"], "x.txt");
    dispatch(dragEvent("drop", filesTransfer([file])));
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith([file]);

    unmount();
    dispatch(dragEvent("drop", filesTransfer([file])));
    expect(second).toHaveBeenCalledTimes(1);
  });
});
