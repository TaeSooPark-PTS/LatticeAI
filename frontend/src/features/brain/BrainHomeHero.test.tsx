import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { makeGraph, makeReadiness } from "@/test/brainFixtures";
import type { MemoryFragment } from "./types";
import { BrainHomeHero, BrainStatsBadge } from "./BrainHomeHero";

// Same detached-ref harness as BrainComposer.test: `useRef` keeps its real
// hook slot but returns a null-reading facade, making the badge's "ref already
// gone" dismiss branch observable.
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

const memory = (id: string): MemoryFragment => ({
  id,
  title: `기억 ${id}`,
  kind: "Note",
  tags: [],
  agentGenerated: false,
});

function renderHero(overrides: Partial<React.ComponentProps<typeof BrainHomeHero>> = {}) {
  const props = {
    language: "ko" as const,
    brainState: "idle" as const,
    intensity: 0.6,
    readiness: makeReadiness(),
    memories: [memory("a"), memory("b")],
    graph: makeGraph(),
    relationshipCount: 9,
    onExploreBrain: vi.fn(),
    ...overrides,
  };
  const view = render(<BrainHomeHero {...props} />);
  return { ...view, props };
}

const badge = () => screen.getByTestId("brain-hero-stats").querySelector("button.brain-hero-stats-badge") as HTMLButtonElement;
const popover = () => screen.queryByTestId("brain-hero-stats-popover");

afterEach(() => {
  detachedRefs.on = false;
  vi.useRealTimers();
});

describe("BrainHomeHero", () => {
  it("leads with the invitation to type, not a metaphor", () => {
    renderHero();
    expect(screen.getByRole("heading", { name: t("ko", "brain.home.askTitle") })).toBeTruthy();
    expect(screen.getByText(t("ko", "brain.hero.line"))).toBeTruthy();
  });

  it("shows the Brain growing from the readiness the screen already has", () => {
    const { container, unmount } = renderHero();
    const host = screen.getByTestId("brain-hero-organism");
    expect(host).toHaveAttribute("data-growth", "alive");
    expect(container.querySelectorAll(".brain-growth-ring")).toHaveLength(3);
    expect(screen.getByText(t("ko", "brain.home.growing"))).toBeTruthy();
    unmount();

    renderHero({ readiness: makeReadiness({ state: "quiet" }) });
    expect(screen.getByTestId("brain-hero-organism")).toHaveAttribute("data-growth", "quiet");
  });

  it("marks a forming Brain as still growing", () => {
    renderHero({ readiness: makeReadiness({ state: "forming" }) });
    expect(screen.getByTestId("brain-hero-organism")).toHaveAttribute("data-growth", "forming");
  });

  it("greets with the empty line when nothing is remembered yet", () => {
    renderHero({
      readiness: makeReadiness({ signals: { memoryCount: 0, conceptCount: 0, relationshipCount: 0, healthySources: 0 } }),
      memories: [],
      graph: makeGraph({ nodes: [], edges: [] }),
    });
    expect(screen.getByText(t("ko", "brain.hero.empty"))).toBeTruthy();
    expect(screen.queryByTestId("brain-hero-stats")).toBeNull();
  });

  it("keeps the badge when only concepts exist, and when only memories exist", () => {
    const { unmount } = renderHero({
      readiness: makeReadiness({ signals: { memoryCount: 0, conceptCount: 0, relationshipCount: 0, healthySources: 0 } }),
      memories: [],
      // concepts > 0 via the graph fallback side of Math.max.
    });
    expect(screen.getByTestId("brain-hero-stats")).toBeTruthy();
    unmount();

    renderHero({
      readiness: makeReadiness({ signals: { memoryCount: 3, conceptCount: 0, relationshipCount: 0, healthySources: 0 } }),
      memories: [],
      graph: makeGraph({ nodes: [], edges: [] }),
    });
    expect(screen.getByTestId("brain-hero-stats")).toBeTruthy();
  });

  it("renders the growing caption in English", () => {
    renderHero({ language: "en" });
    const caption = screen.getByText(t("en", "brain.home.growing"));
    expect(caption.textContent).toBe("Your memory is growing");
  });

  it("renders the trailing slot only when given", () => {
    const { container, unmount } = renderHero({ trailing: <em data-testid="hero-trailing">모델 없음</em> });
    expect(screen.getByTestId("hero-trailing")).toBeTruthy();
    expect(container.querySelector(".brain-hero-trailing")).toBeTruthy();
    unmount();

    const { container: bare } = renderHero();
    expect(bare.querySelector(".brain-hero-trailing")).toBeNull();
  });

  it("lets the Brain organism hand off to the map", () => {
    const { props } = renderHero();
    fireEvent.click(screen.getByTestId("living-brain"));
    expect(props.onExploreBrain).toHaveBeenCalled();
  });
});

describe("BrainStatsBadge", () => {
  function renderBadge(overrides: Partial<React.ComponentProps<typeof BrainStatsBadge>> = {}) {
    const props = {
      language: "ko" as const,
      memories: 4,
      concepts: 6,
      relationships: 9,
      onExploreBrain: vi.fn(),
      ...overrides,
    };
    const view = render(<BrainStatsBadge {...props} />);
    return { ...view, props };
  }

  it("opens on click, guards the immediate re-click, then closes once seen", () => {
    vi.useFakeTimers();
    renderBadge();
    expect(popover()).toBeNull();
    expect(badge()).toHaveAttribute("aria-expanded", "false");
    expect(badge()).not.toHaveAttribute("aria-controls");

    fireEvent.click(badge());
    expect(popover()).toBeTruthy();
    expect(badge()).toHaveAttribute("aria-controls", popover()!.id);

    act(() => {
      vi.advanceTimersByTime(100);
    });
    fireEvent.click(badge());
    expect(popover()).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(300);
    });
    fireEvent.click(badge());
    expect(popover()).toBeNull();
  });

  it("opens on mouse hover only and closes after the debounced leave", () => {
    vi.useFakeTimers();
    renderBadge();
    const root = screen.getByTestId("brain-hero-stats");

    fireEvent.pointerOver(root, { pointerType: "touch" });
    expect(popover()).toBeNull();

    fireEvent.pointerOver(root, { pointerType: "mouse" });
    expect(popover()).toBeTruthy();
    // Re-entering keeps it open without resetting the opened-at stamp.
    fireEvent.pointerOver(root, { pointerType: "mouse" });

    fireEvent.pointerOut(root, { pointerType: "touch" });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(popover()).toBeTruthy();

    fireEvent.pointerOut(root, { pointerType: "mouse" });
    expect(popover()).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(160);
    });
    expect(popover()).toBeNull();
  });

  it("cancels the scheduled close when the pointer returns", () => {
    vi.useFakeTimers();
    renderBadge();
    const root = screen.getByTestId("brain-hero-stats");
    fireEvent.pointerOver(root, { pointerType: "mouse" });
    fireEvent.pointerOut(root, { pointerType: "mouse" });
    fireEvent.pointerOver(root, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(popover()).toBeTruthy();
  });

  it("dismisses on outside pointerdown and outside focus, but not from inside", () => {
    renderBadge();
    fireEvent.click(badge());
    fireEvent.pointerDown(badge());
    expect(popover()).toBeTruthy();

    fireEvent.pointerDown(document.body);
    expect(popover()).toBeNull();

    fireEvent.click(badge());
    fireEvent.focusIn(document.body);
    expect(popover()).toBeNull();
  });

  it("dismisses for a synthetic event without a target", () => {
    renderBadge();
    fireEvent.click(badge());
    const event = new Event("pointerdown", { bubbles: true });
    Object.defineProperty(event, "target", { value: null });
    fireEvent(document.body, event);
    expect(popover()).toBeNull();
  });

  it("treats any event as outside once its ref is detached", () => {
    detachedRefs.on = true;
    renderBadge();
    fireEvent.click(badge());
    expect(popover()).toBeTruthy();
    // Inside the badge — but the detached ref cannot vouch for that.
    fireEvent.pointerDown(badge());
    expect(popover()).toBeNull();
  });

  it("closes on Escape and ignores other keys", () => {
    renderBadge();
    fireEvent.click(badge());
    fireEvent.keyDown(badge(), { key: "a" });
    expect(popover()).toBeTruthy();
    fireEvent.keyDown(badge(), { key: "Escape" });
    expect(popover()).toBeNull();
  });

  it("draws each row proportional to the largest count", () => {
    renderBadge();
    fireEvent.click(badge());
    const fills = popover()!.querySelectorAll<HTMLElement>(".brain-hero-stats-fill");
    expect(fills[0].style.width).toBe("44%");
    expect(fills[1].style.width).toBe("67%");
    expect(fills[2].style.width).toBe("100%");
  });

  it("floors empty rows at the visible minimum instead of dividing by zero", () => {
    renderBadge({ memories: 0, concepts: 0, relationships: 0 });
    fireEvent.click(badge());
    for (const fill of popover()!.querySelectorAll<HTMLElement>(".brain-hero-stats-fill")) {
      expect(fill.style.width).toBe("6%");
    }
  });

  it("hands the popover CTA to the map", () => {
    const { props } = renderBadge();
    fireEvent.click(badge());
    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.hero.stats.mapCta")) }));
    expect(props.onExploreBrain).toHaveBeenCalledTimes(1);
  });
});
