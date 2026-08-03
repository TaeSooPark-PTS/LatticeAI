import * as React from "react";

/**
 * Distance from the bottom, in CSS pixels, still read as "parked at the newest
 * line". Wide enough to survive sub-pixel rounding and the sticky composer
 * resizing under the stream, narrow enough that one deliberate scroll up
 * counts as walking away.
 */
const NEAR_BOTTOM_PX = 64;

type Scroller = {
  /** The element whose `scrollTop` moves the content. */
  element: HTMLElement;
  /** True when `element` is the document scroller, which reports on the view. */
  isDocument: boolean;
};

function scrolls(element: HTMLElement): boolean {
  if (element.scrollHeight - element.clientHeight <= 1) return false;
  const overflow = element.ownerDocument.defaultView?.getComputedStyle(element).overflowY;
  return overflow === "auto" || overflow === "scroll" || overflow === "overlay";
}

/**
 * Finds the box that actually scrolls this content. `.brain-stream` is its own
 * scroll box on the empty home but turns `overflow: visible` once a
 * conversation starts, at which point the page scrolls instead — writing to
 * the wrong element is a silent no-op, so resolve it rather than assume it.
 */
function resolveScroller(start: HTMLElement | null): Scroller | null {
  for (let node: HTMLElement | null = start; node; node = node.parentElement) {
    if (scrolls(node)) return { element: node, isDocument: false };
  }
  // `scrollingElement` is null in quirks mode, and jsdom reports null even in
  // standards mode. The document element is still what scrolls, so falling
  // back to it keeps the follow working instead of silently doing nothing.
  const doc = start?.ownerDocument;
  const root = doc?.scrollingElement ?? doc?.documentElement ?? null;
  if (!(root instanceof HTMLElement)) return null;
  return { element: root, isDocument: true };
}

function isNearBottom(element: HTMLElement): boolean {
  return element.scrollHeight - element.clientHeight - element.scrollTop <= NEAR_BOTTOM_PX;
}

/**
 * Keeps a growing list pinned to its newest content *only while the reader is
 * still down there*. Streaming answers used to re-pin on every token, so
 * scrolling up to re-read an earlier paragraph yanked the view back down mid
 * answer.
 *
 * Accessibility: this moves the reading position only — focus is never taken,
 * and the scroll is a plain `scrollTop` assignment so CSS decides smooth vs
 * instant, which is how `prefers-reduced-motion` is already honoured
 * (`experience/responsive.css` forces `scroll-behavior: auto` under it).
 *
 * @param dependency Value that changes whenever new content is appended.
 * @returns Ref to attach to the content element.
 */
export function useStickyBottom<T extends HTMLElement>(dependency: unknown): React.RefObject<T | null> {
  const ref = React.useRef<T>(null);
  const scrollerRef = React.useRef<Scroller | null>(null);
  const stuckRef = React.useRef(true);
  const lastTopRef = React.useRef(0);

  const ensureScroller = React.useCallback(() => {
    const cached = scrollerRef.current;
    if (cached && cached.element.isConnected) return cached;
    scrollerRef.current = resolveScroller(ref.current);
    return scrollerRef.current;
  }, []);

  React.useEffect(() => {
    // Capture phase on the document: scroll events do not bubble, so this is
    // the one listener that hears both an inner scroll box and the page.
    const onScroll = (event: Event) => {
      const scroller = scrollerRef.current;
      if (!scroller) return;
      const fromScroller = scroller.isDocument
        ? event.target === scroller.element.ownerDocument || event.target === scroller.element
        : event.target === scroller.element;
      if (!fromScroller) return;
      const top = scroller.element.scrollTop;
      const movedUp = top < lastTopRef.current - 1;
      lastTopRef.current = top;
      // Downward movement never stops the follow: it is either the reader
      // returning to the newest line or our own scroll settling.
      if (movedUp) stuckRef.current = isNearBottom(scroller.element);
      else if (isNearBottom(scroller.element)) stuckRef.current = true;
    };
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    return () => document.removeEventListener("scroll", onScroll, { capture: true });
  }, []);

  React.useEffect(() => {
    const scroller = ensureScroller();
    if (!scroller || !stuckRef.current) return;
    scroller.element.scrollTop = scroller.element.scrollHeight;
    lastTopRef.current = scroller.element.scrollTop;
  }, [dependency, ensureScroller]);

  return ref;
}
