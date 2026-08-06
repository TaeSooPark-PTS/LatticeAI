import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { LivingBrain, triggerBrainRecall, type BrainState } from "./LivingBrain";

/**
 * The Brain is the product's other participant, and its behaviours are wired
 * by hand: pulse timers, a recall event bus, pointer tilt through rAF, and a
 * reduced-motion gate. Each of those is driven here with the environment
 * pieces (rAF, matchMedia, timers) supplied per test so nothing depends on a
 * real compositor.
 */

function organism() {
  return screen.getByTestId("living-brain") as HTMLButtonElement;
}

function rect(button: HTMLElement) {
  vi.spyOn(button, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    width: 100,
    height: 100,
    right: 100,
    bottom: 100,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

beforeEach(() => {
  useAppStore.setState({ language: "ko" });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("LivingBrain", () => {
  it("breathes with sane defaults and labels its idle state", () => {
    render(<LivingBrain />);
    const button = organism();
    expect(button.className).toContain("size-large");
    expect(button.className).toContain("depth-0");
    expect(button.dataset.state).toBe("idle");
    expect(button.getAttribute("aria-busy")).toBe("false");
    expect(button.getAttribute("aria-label")).toBe(t("ko", "brain.living.open"));
    expect(screen.getByText(t("ko", "brain.living.state.idle"))).toBeTruthy();
    // One quiet memory ripple, five thought particles at depth zero.
    expect(document.querySelectorAll(".memory-ripple")).toHaveLength(1);
    expect(document.querySelectorAll(".thought-particle")).toHaveLength(5);
    expect(document.querySelector(".thought-particle.resolving")).toBeNull();
    expect((document.querySelector(".brain-aura") as HTMLElement).style.animationDuration).toBe("6.8s");
  });

  it.each([
    ["listening", "brain.living.state.listening", false],
    ["thinking", "brain.living.state.thinking", true],
    ["recalling", "brain.living.state.recalling", true],
    ["synthesizing", "brain.living.state.synthesizing", true],
    ["planning", "brain.living.state.planning", false],
    ["acting", "brain.living.state.acting", true],
    ["resting", "brain.living.state.resting", false],
  ] as Array<[BrainState, string, boolean]>)(
    "labels the %s state and reports business honestly",
    (state, key, busy) => {
      vi.useFakeTimers();
      render(<LivingBrain state={state} intensity={0.5} />);
      expect(screen.getByText(t("ko", key))).toBeTruthy();
      expect(organism().getAttribute("aria-busy")).toBe(String(busy));
    },
  );

  it("breathes faster while thinking and slower while recalling", () => {
    vi.useFakeTimers();
    const { rerender } = render(<LivingBrain state="thinking" />);
    const aura = () => (document.querySelector(".brain-aura") as HTMLElement).style.animationDuration;
    expect(aura()).toBe("1.65s");
    rerender(<LivingBrain state="recalling" />);
    expect(aura()).toBe("2.4s");
  });

  it("takes an explicit label over the state text and can hide it entirely", () => {
    const { rerender } = render(<LivingBrain label="나의 두뇌" />);
    expect(screen.getByText("나의 두뇌")).toBeTruthy();

    rerender(<LivingBrain showLabel={false} />);
    expect(document.querySelector(".brain-presence-label")).toBeNull();

    // Trace size is a wordmark: never a label, even when asked for one.
    rerender(<LivingBrain size="trace" showLabel />);
    expect(document.querySelector(".brain-presence-label")).toBeNull();
    expect(document.querySelector(".brain-presence")?.className).toContain("trace");

    rerender(<LivingBrain size="normal" className="custom-host" />);
    expect(organism().className).toContain("size-normal");
    const presence = document.querySelector(".brain-presence") as HTMLElement;
    expect(presence.className).toContain("custom-host");
    expect(presence.className).not.toContain("large");
  });

  it("opens with depth: more ripples, resolving thoughts, a wider glow", () => {
    const { rerender } = render(<LivingBrain depth={3} />);
    const presence = () => document.querySelector(".brain-presence") as HTMLElement;
    expect(presence().className).toContain("is-exploring");
    expect(presence().dataset.depth).toBe("3");
    expect(document.querySelectorAll(".memory-ripple")).toHaveLength(3);
    expect(document.querySelectorAll(".thought-particle.resolving").length).toBeGreaterThan(0);
    expect((document.querySelector(".brain-aura") as HTMLElement).style.boxShadow).toContain("var(--aura-blur");
    expect(screen.getByText(t("ko", "brain.living.depth", { depth: 3 }))).toBeTruthy();

    // Full depth flips the invitation from "open" to "see the graph".
    rerender(<LivingBrain depth={5} />);
    expect(organism().getAttribute("aria-label")).toBe(t("ko", "brain.living.graph"));
    expect(document.querySelectorAll(".memory-ripple")).toHaveLength(4);
    expect(document.querySelectorAll(".thought-particle")).toHaveLength(12);

    // Depth zero keeps the aura shadowless.
    rerender(<LivingBrain depth={0} />);
    expect((document.querySelector(".brain-aura") as HTMLElement).style.boxShadow).toBe("none");
  });

  it("pulses on click, tells the parent, and travels deeper only when idle", () => {
    vi.useFakeTimers();
    const onPulse = vi.fn();
    const onInteract = vi.fn();
    render(<LivingBrain onPulse={onPulse} onInteract={onInteract} />);
    const button = organism();

    fireEvent.click(button);
    expect(button.classList.contains("pulse")).toBe(true);
    expect(onPulse).toHaveBeenCalledTimes(1);
    expect(onInteract).toHaveBeenCalledTimes(1);

    // A second click within the window clears and re-arms the pulse timer.
    fireEvent.click(button);
    act(() => {
      vi.advanceTimersByTime(1350);
    });
    expect(button.classList.contains("pulse")).toBe(false);
  });

  it("stays touchable while busy but refuses to navigate away", () => {
    vi.useFakeTimers();
    const onInteract = vi.fn();
    render(<LivingBrain state="thinking" onInteract={onInteract} />);
    fireEvent.click(organism());
    expect(organism().classList.contains("pulse")).toBe(true);
    expect(onInteract).not.toHaveBeenCalled();
  });

  it("survives a click with no listeners wired at all", () => {
    vi.useFakeTimers();
    render(<LivingBrain />);
    fireEvent.click(organism());
    expect(organism().classList.contains("pulse")).toBe(true);
  });

  it("pulses when anything broadcasts a recall", () => {
    vi.useFakeTimers();
    render(<LivingBrain />);
    act(() => {
      triggerBrainRecall();
    });
    expect(organism().classList.contains("pulse")).toBe(true);
  });

  it("does nothing when recall is triggered outside a window", () => {
    // Server-side callers must be a no-op, not a crash.
    vi.stubGlobal("window", undefined);
    expect(() => triggerBrainRecall()).not.toThrow();
    vi.unstubAllGlobals();
  });

  it("pulses by itself while recalling or at high intensity, but not at rest", () => {
    vi.useFakeTimers();
    const { unmount } = render(<LivingBrain state="recalling" />);
    act(() => {
      vi.advanceTimersByTime(180);
    });
    expect(organism().classList.contains("pulse")).toBe(true);
    unmount();

    render(<LivingBrain state="idle" intensity={0.9} />);
    act(() => {
      vi.advanceTimersByTime(180);
    });
    expect(organism().classList.contains("pulse")).toBe(true);
  });

  it("stays calm at ordinary intensity", () => {
    vi.useFakeTimers();
    render(<LivingBrain state="idle" intensity={0.5} />);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(organism().classList.contains("pulse")).toBe(false);
  });

  describe("pointer tilt", () => {
    function stubFrames() {
      const frames: FrameRequestCallback[] = [];
      vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
        frames.push(cb);
        return frames.length;
      });
      const cancel = vi.fn();
      vi.stubGlobal("cancelAnimationFrame", cancel);
      return { frames, cancel };
    }

    it("leans toward the pointer once the next frame paints", () => {
      const { frames } = stubFrames();
      render(<LivingBrain />);
      const button = organism();
      rect(button);

      fireEvent.pointerMove(button, { pointerType: "mouse", clientX: 75, clientY: 25 });
      expect(frames).toHaveLength(1);
      // A second move before the frame lands coalesces into the same frame.
      fireEvent.pointerMove(button, { pointerType: "mouse", clientX: 100, clientY: 0 });
      expect(frames).toHaveLength(1);

      act(() => {
        frames[0](0);
      });
      // The latest pending position wins: nx=1 -> y=11deg, ny=-1 -> x=9deg.
      expect(button.style.getPropertyValue("--tilt-y")).toBe("11.00deg");
      expect(button.style.getPropertyValue("--tilt-x")).toBe("9.00deg");

      // With the frame consumed, the next move schedules a fresh one.
      fireEvent.pointerMove(button, { pointerType: "mouse", clientX: 50, clientY: 50 });
      expect(frames).toHaveLength(2);
    });

    it("cancels a pending frame and stands upright when the pointer leaves", () => {
      const { frames, cancel } = stubFrames();
      render(<LivingBrain />);
      const button = organism();
      rect(button);

      fireEvent.pointerMove(button, { pointerType: "mouse", clientX: 75, clientY: 25 });
      fireEvent.pointerLeave(button);
      expect(cancel).toHaveBeenCalledWith(1);
      expect(button.style.getPropertyValue("--tilt-y")).toBe("0deg");
      expect(button.style.getPropertyValue("--tilt-x")).toBe("0deg");

      // Leaving again with nothing pending cancels nothing.
      fireEvent.pointerLeave(button);
      expect(cancel).toHaveBeenCalledTimes(1);
      expect(frames).toHaveLength(1);
    });

    it("ignores touch pointers, which have no hover to speak of", () => {
      const { frames } = stubFrames();
      render(<LivingBrain />);
      fireEvent.pointerMove(organism(), { pointerType: "touch", clientX: 10, clientY: 10 });
      expect(frames).toHaveLength(0);
    });

    it("holds still for people who asked for reduced motion", () => {
      const query = {
        matches: true,
        media: "(prefers-reduced-motion: reduce)",
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      };
      vi.spyOn(window, "matchMedia").mockReturnValue(query as unknown as MediaQueryList);
      const { frames } = stubFrames();
      const { unmount } = render(<LivingBrain />);
      expect(query.addEventListener).toHaveBeenCalledWith("change", expect.any(Function));

      fireEvent.pointerMove(organism(), { pointerType: "mouse", clientX: 10, clientY: 10 });
      expect(frames).toHaveLength(0);

      unmount();
      expect(query.removeEventListener).toHaveBeenCalledWith("change", expect.any(Function));
    });

    it("drops a frame that lands after the organism is gone", () => {
      const { frames } = stubFrames();
      const { unmount } = render(<LivingBrain />);
      const button = organism();
      rect(button);
      fireEvent.pointerMove(button, { pointerType: "mouse", clientX: 75, clientY: 25 });
      expect(frames).toHaveLength(1);

      unmount();
      // The stubbed cancel let the frame survive; firing it now must hit the
      // unmounted-ref guard, not a crash.
      expect(() => frames[0](0)).not.toThrow();
      expect(button.style.getPropertyValue("--tilt-y")).toBe("");
    });
  });

  describe("teardown races", () => {
    it("ignores a recall event that outlives the component", () => {
      // Simulate a listener that could not be removed in time.
      vi.spyOn(window, "removeEventListener").mockImplementation(() => {});
      const { unmount } = render(<LivingBrain />);
      unmount();
      expect(() => {
        act(() => {
          triggerBrainRecall();
        });
      }).not.toThrow();
    });

    it("ignores a pulse timer that outlives the component", () => {
      vi.useFakeTimers();
      vi.spyOn(window, "clearTimeout").mockImplementation((() => {}) as typeof window.clearTimeout);
      const { unmount } = render(<LivingBrain />);
      fireEvent.click(organism());
      // Unmount "clears" the timer, but our stub kept it alive on purpose.
      unmount();
      expect(() => {
        act(() => {
          vi.advanceTimersByTime(1350);
        });
      }).not.toThrow();
    });
  });
});
