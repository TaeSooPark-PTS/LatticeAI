import * as React from "react";

const NEAR_BOTTOM_PX = 64;

type Scroller = {
  element: HTMLElement;
  isDocument: boolean;
};

function scrolls(element: HTMLElement): boolean {
  if (element.scrollHeight - element.clientHeight <= 1) return false;
  const overflow = element.ownerDocument.defaultView?.getComputedStyle(element).overflowY;
  return overflow === "auto" || overflow === "scroll" || overflow === "overlay";
}

function resolveScroller(start: HTMLElement | null): Scroller | null {
  for (let node: HTMLElement | null = start; node; node = node.parentElement) {
    if (scrolls(node)) return { element: node, isDocument: false };
  }
  const doc = start?.ownerDocument;
  const root = doc?.scrollingElement ?? doc?.documentElement ?? null;
  if (!(root instanceof HTMLElement)) return null;
  return { element: root, isDocument: true };
}

function isNearBottom(element: HTMLElement): boolean {
  return element.scrollHeight - element.clientHeight - element.scrollTop <= NEAR_BOTTOM_PX;
}

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
