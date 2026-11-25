import * as React from "react";
import { fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useStickyBottom } from "./useStickyBottom";

type ScrollBox = { scrollHeight: number; clientHeight: number; top: number };

function patchScrollBox(element: HTMLElement, box: ScrollBox) {
  Object.defineProperty(element, "scrollHeight", { configurable: true, get: () => box.scrollHeight });
  Object.defineProperty(element, "clientHeight", { configurable: true, get: () => box.clientHeight });
  Object.defineProperty(element, "scrollTop", {
    configurable: true,
    get: () => box.top,
    set: (value: number) => {
      box.top = Math.max(0, Math.min(value, box.scrollHeight - box.clientHeight));
    },
  });
}

function unpatchScrollBox(element: HTMLElement) {
  for (const key of ["scrollHeight", "clientHeight", "scrollTop"]) {
    Reflect.deleteProperty(element, key);
  }
}

function Harness({ items, box, scroller }: { items: number; box: ScrollBox; scroller: "element" | "page" }) {
  const streamRef = useStickyBottom<HTMLDivElement>(items);
  React.useLayoutEffect(() => {
    if (scroller === "page") {
      patchScrollBox(document.documentElement, box);
      return;
    }
    const node = streamRef.current;
    if (!node) return;
    node.style.overflowY = "auto";
    patchScrollBox(node, box);
  }, []);

  return (
    <div ref={streamRef} data-testid="stream">
      <input aria-label="composer" />
      {Array.from({ length: items }, (_, index) => <p key={index}>message {index}</p>)}
    </div>
  );
}

afterEach(() => unpatchScrollBox(document.documentElement));

describe("useStickyBottom", () => {
  it("follows new content while the reader is parked at the bottom", () => {
    const box: ScrollBox = { scrollHeight: 1000, clientHeight: 400, top: 0 };
    const { rerender } = render(<Harness items={2} box={box} scroller="element" />);

    expect(box.top).toBe(600);

    box.scrollHeight = 1400;
    rerender(<Harness items={3} box={box} scroller="element" />);

    expect(box.top).toBe(1000);
  });

  it("stops following once the reader scrolls away, and resumes at the bottom", () => {
    const box: ScrollBox = { scrollHeight: 1400, clientHeight: 400, top: 0 };
    const { getByTestId, rerender } = render(<Harness items={3} box={box} scroller="element" />);
    const stream = getByTestId("stream");
    expect(box.top).toBe(1000);

    stream.scrollTop = 200;
    fireEvent.scroll(stream);

    box.scrollHeight = 1800;
    rerender(<Harness items={4} box={box} scroller="element" />);
    expect(box.top).toBe(200);

    stream.scrollTop = 1400;
    fireEvent.scroll(stream);

    box.scrollHeight = 2200;
    rerender(<Harness items={5} box={box} scroller="element" />);
    expect(box.top).toBe(1800);
  });

  it("treats a nudge inside the near-bottom threshold as still following", () => {
    const box: ScrollBox = { scrollHeight: 1000, clientHeight: 400, top: 0 };
    const { getByTestId, rerender } = render(<Harness items={2} box={box} scroller="element" />);
    const stream = getByTestId("stream");

    stream.scrollTop = 560;
    fireEvent.scroll(stream);
    box.scrollHeight = 1400;
    rerender(<Harness items={3} box={box} scroller="element" />);
    expect(box.top).toBe(1000);

    stream.scrollTop = 900;
    fireEvent.scroll(stream);
    box.scrollHeight = 1800;
    rerender(<Harness items={4} box={box} scroller="element" />);
    expect(box.top).toBe(900);
  });

  it("follows the page when the content element is not the scroll box", () => {
    const box: ScrollBox = { scrollHeight: 3000, clientHeight: 800, top: 0 };
    const { rerender } = render(<Harness items={2} box={box} scroller="page" />);

    expect(box.top).toBe(2200);

    document.documentElement.scrollTop = 100;
    fireEvent.scroll(document);

    box.scrollHeight = 3600;
    rerender(<Harness items={3} box={box} scroller="page" />);
    expect(box.top).toBe(100);
  });

  it("never moves focus while following", () => {
    const box: ScrollBox = { scrollHeight: 1000, clientHeight: 400, top: 0 };
    const { getByLabelText, rerender } = render(<Harness items={2} box={box} scroller="element" />);
    const composer = getByLabelText("composer");
    composer.focus();

    box.scrollHeight = 1400;
    rerender(<Harness items={3} box={box} scroller="element" />);

    expect(box.top).toBe(1000);
    expect(document.activeElement).toBe(composer);
  });

  it("does nothing at all when its ref was never attached", () => {
    function Detached({ items }: { items: number }) {
      useStickyBottom<HTMLDivElement>(items);
      return <p>no stream</p>;
    }
    const { rerender } = render(<Detached items={1} />);
    fireEvent.scroll(document);
    rerender(<Detached items={2} />);
    expect(document.documentElement.scrollTop).toBe(0);
  });

  it("ignores scroll noise from unrelated boxes", () => {
    const box: ScrollBox = { scrollHeight: 1400, clientHeight: 400, top: 0 };
    const { getByTestId, rerender } = render(<Harness items={3} box={box} scroller="element" />);
    const stream = getByTestId("stream");
    expect(box.top).toBe(1000);

    const other = document.createElement("div");
    document.body.appendChild(other);
    stream.scrollTop = 200;
    fireEvent.scroll(other);
    other.remove();

    box.scrollHeight = 1800;
    rerender(<Harness items={4} box={box} scroller="element" />);
    expect(box.top).toBe(1400);
  });

  it("hears the page scroller when the event fires on the document element", () => {
    const box: ScrollBox = { scrollHeight: 3000, clientHeight: 800, top: 0 };
    const { rerender } = render(<Harness items={2} box={box} scroller="page" />);
    expect(box.top).toBe(2200);

    document.documentElement.scrollTop = 100;
    fireEvent.scroll(document.documentElement);

    box.scrollHeight = 3600;
    rerender(<Harness items={3} box={box} scroller="page" />);
    expect(box.top).toBe(100);
  });

  it("scrolling further down while still away from the bottom stays unpinned", () => {
    const box: ScrollBox = { scrollHeight: 1400, clientHeight: 400, top: 0 };
    const { getByTestId, rerender } = render(<Harness items={3} box={box} scroller="element" />);
    const stream = getByTestId("stream");
    expect(box.top).toBe(1000);

    stream.scrollTop = 100;
    fireEvent.scroll(stream);
    stream.scrollTop = 200;
    fireEvent.scroll(stream);

    box.scrollHeight = 1800;
    rerender(<Harness items={4} box={box} scroller="element" />);
    expect(box.top).toBe(200);
  });
});
