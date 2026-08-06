/**
 * The accessibility contract every modal in this app depends on: Tab cycles
 * inside the dialog, Escape closes it from anywhere inside, and focus returns
 * to whatever opened it. A keyboard user who falls out of a modal has no way
 * back, so each edge — an empty dialog, focus already outside it, an inactive
 * trap — is asserted rather than assumed.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { useFocusTrap } from "./useFocusTrap";

function Trapped({
  onEscape,
  active,
  attach = true,
  rootTabIndex,
  children,
}: {
  onEscape?: () => void;
  active: boolean;
  attach?: boolean;
  rootTabIndex?: number;
  children?: React.ReactNode;
}) {
  const ref = useFocusTrap<HTMLDivElement>(onEscape, active);
  return (
    <div ref={attach ? ref : undefined} data-testid="dialog" tabIndex={rootTabIndex}>
      {children}
    </div>
  );
}

/** The shape used by dialogs that only exist while open — `active` omitted. */
function AlwaysTrapped({ onEscape }: { onEscape?: () => void }) {
  const ref = useFocusTrap<HTMLDivElement>(onEscape);
  return (
    <div ref={ref} data-testid="dialog">
      <button type="button">유일</button>
    </div>
  );
}

function withOpener() {
  const opener = document.createElement("button");
  document.body.appendChild(opener);
  opener.focus();
  return opener;
}

describe("useFocusTrap on activation", () => {
  it("focuses the first focusable child and returns focus to the opener on close", () => {
    const opener = withOpener();
    const { unmount } = render(
      <Trapped active>
        <button type="button">첫 번째</button>
        <button type="button">마지막</button>
      </Trapped>,
    );
    expect(document.activeElement).toBe(screen.getByText("첫 번째"));

    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("uses the default active=true when the caller omits it", () => {
    render(<AlwaysTrapped />);
    expect(document.activeElement).toBe(screen.getByText("유일"));
  });

  it("focuses the container itself when nothing inside can take focus", () => {
    render(
      <Trapped active>
        <p>읽기 전용 안내</p>
      </Trapped>,
    );
    expect(document.activeElement).toBe(screen.getByTestId("dialog"));
  });

  it("gives the container a tabindex only when it has none of its own", () => {
    const { unmount } = render(<Trapped active />);
    expect(screen.getByTestId("dialog").getAttribute("tabindex")).toBe("-1");
    unmount();

    render(<Trapped active rootTabIndex={0} />);
    expect(screen.getByTestId("dialog").getAttribute("tabindex")).toBe("0");
  });

  it("skips hidden and aria-hidden candidates when choosing the first target", () => {
    render(
      <Trapped active>
        <div hidden>
          <button type="button">숨김</button>
        </div>
        <button type="button" aria-hidden="true">보조기기에서 숨김</button>
        <button type="button">진짜 첫 번째</button>
      </Trapped>,
    );
    expect(document.activeElement).toBe(screen.getByText("진짜 첫 번째"));
  });

  it("does nothing at all while inactive", () => {
    const opener = withOpener();
    const onEscape = vi.fn();
    render(
      <Trapped active={false} onEscape={onEscape}>
        <button type="button">첫 번째</button>
      </Trapped>,
    );
    expect(document.activeElement).toBe(opener);
    fireEvent.keyDown(screen.getByTestId("dialog"), { key: "Escape" });
    expect(onEscape).not.toHaveBeenCalled();
    opener.remove();
  });

  it("tolerates a ref that was never attached to anything", () => {
    const opener = withOpener();
    expect(() =>
      render(
        <Trapped active attach={false}>
          <button type="button">첫 번째</button>
        </Trapped>,
      ),
    ).not.toThrow();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("tolerates there being no previously focused element to restore", () => {
    const activeElement = Object.getOwnPropertyDescriptor(Document.prototype, "activeElement");
    Object.defineProperty(document, "activeElement", { configurable: true, get: () => null });
    const { unmount } = render(
      <Trapped active>
        <button type="button">첫 번째</button>
      </Trapped>,
    );
    expect(() => unmount()).not.toThrow();
    delete (document as { activeElement?: unknown }).activeElement;
    expect(activeElement).toBeTruthy();
    expect(document.activeElement).toBeTruthy();
  });
});

describe("useFocusTrap key handling", () => {
  it("closes on Escape and stops the key from escaping the dialog", () => {
    const onEscape = vi.fn();
    const onOuterKey = vi.fn();
    document.addEventListener("keydown", onOuterKey);
    render(
      <Trapped active onEscape={onEscape}>
        <button type="button">첫 번째</button>
      </Trapped>,
    );

    const notCancelled = fireEvent.keyDown(screen.getByText("첫 번째"), { key: "Escape" });
    expect(onEscape).toHaveBeenCalledTimes(1);
    expect(notCancelled).toBe(false); // preventDefault()
    expect(onOuterKey).not.toHaveBeenCalled(); // stopPropagation()
    document.removeEventListener("keydown", onOuterKey);
  });

  it("lets Escape through when the dialog offered no close handler", () => {
    const onOuterKey = vi.fn();
    document.addEventListener("keydown", onOuterKey);
    render(
      <Trapped active>
        <button type="button">첫 번째</button>
      </Trapped>,
    );
    fireEvent.keyDown(screen.getByText("첫 번째"), { key: "Escape" });
    expect(onOuterKey).toHaveBeenCalledTimes(1);
    document.removeEventListener("keydown", onOuterKey);
  });

  it("ignores every key that is neither Escape nor Tab", () => {
    render(
      <Trapped active>
        <button type="button">첫 번째</button>
      </Trapped>,
    );
    const first = screen.getByText("첫 번째");
    expect(fireEvent.keyDown(first, { key: "a" })).toBe(true);
    expect(document.activeElement).toBe(first);
  });

  it("parks focus on the container when there is nothing to cycle through", () => {
    render(
      <Trapped active>
        <p>내용 없음</p>
      </Trapped>,
    );
    const dialog = screen.getByTestId("dialog");
    expect(fireEvent.keyDown(dialog, { key: "Tab" })).toBe(false);
    expect(document.activeElement).toBe(dialog);
  });

  it("wraps Tab from the last element back to the first", () => {
    render(
      <Trapped active>
        <button type="button">첫 번째</button>
        <button type="button">가운데</button>
        <button type="button">마지막</button>
      </Trapped>,
    );
    const last = screen.getByText("마지막");
    last.focus();
    expect(fireEvent.keyDown(last, { key: "Tab" })).toBe(false);
    expect(document.activeElement).toBe(screen.getByText("첫 번째"));
  });

  it("leaves Tab alone in the middle of the dialog", () => {
    render(
      <Trapped active>
        <button type="button">첫 번째</button>
        <button type="button">가운데</button>
        <button type="button">마지막</button>
      </Trapped>,
    );
    const middle = screen.getByText("가운데");
    middle.focus();
    expect(fireEvent.keyDown(middle, { key: "Tab" })).toBe(true);
    expect(document.activeElement).toBe(middle);
  });

  it("pulls focus back in when Tab is pressed from outside the dialog", () => {
    const outside = withOpener();
    render(
      <Trapped active>
        <button type="button">첫 번째</button>
        <button type="button">마지막</button>
      </Trapped>,
    );
    outside.focus();
    fireEvent.keyDown(screen.getByTestId("dialog"), { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByText("첫 번째"));
    outside.remove();
  });

  it("wraps Shift+Tab from the first element to the last", () => {
    render(
      <Trapped active>
        <button type="button">첫 번째</button>
        <button type="button">가운데</button>
        <button type="button">마지막</button>
      </Trapped>,
    );
    const first = screen.getByText("첫 번째");
    expect(fireEvent.keyDown(first, { key: "Tab", shiftKey: true })).toBe(false);
    expect(document.activeElement).toBe(screen.getByText("마지막"));
  });

  it("wraps Shift+Tab from the container itself to the last element", () => {
    render(
      <Trapped active>
        <button type="button">첫 번째</button>
        <button type="button">마지막</button>
      </Trapped>,
    );
    const dialog = screen.getByTestId("dialog");
    dialog.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(screen.getByText("마지막"));
  });

  it("pulls focus back in when Shift+Tab is pressed from outside", () => {
    const outside = withOpener();
    render(
      <Trapped active>
        <button type="button">첫 번째</button>
        <button type="button">마지막</button>
      </Trapped>,
    );
    outside.focus();
    fireEvent.keyDown(screen.getByTestId("dialog"), { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(screen.getByText("마지막"));
    outside.remove();
  });

  it("leaves Shift+Tab alone in the middle of the dialog", () => {
    render(
      <Trapped active>
        <button type="button">첫 번째</button>
        <button type="button">가운데</button>
        <button type="button">마지막</button>
      </Trapped>,
    );
    const middle = screen.getByText("가운데");
    middle.focus();
    expect(fireEvent.keyDown(middle, { key: "Tab", shiftKey: true })).toBe(true);
    expect(document.activeElement).toBe(middle);
  });
});
