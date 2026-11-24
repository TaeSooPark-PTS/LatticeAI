import * as React from "react";
import { type BrainState } from "@/components/LivingBrain";
import { parseHash } from "@/routes";
import { useAppStore } from "@/store/appStore";
import { clamp } from "@/lib/utils";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Focusable descendants that are actually rendered.
 *
 * The menu holds a copy of the management links that CSS hides once the topbar
 * shows them. Those anchors stay in the DOM, so a plain querySelectorAll would
 * report focus targets the browser will refuse to focus and Tab will skip —
 * putting the focus trap's boundaries in the wrong place.
 */
export function focusablesIn(root: HTMLElement | null): HTMLElement[] {
  return Array.from(root?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? []).filter(
    (element) => element.offsetWidth > 0 || element.offsetHeight > 0 || element.getClientRects().length > 0,
  );
}

/** Exported for tests: focus a target that may already have gone away. */
export function restoreFocusTo(element: HTMLElement | null) {
  element?.focus();
}

/** Exported for tests: whether a pointer-down landed on the menu or one of its triggers. */
export function pointerDownHitsMenu(
  target: Node,
  panel: HTMLElement | null,
  desktopButton: HTMLElement | null,
  mobileButton: HTMLElement | null,
) {
  return Boolean(
    panel?.contains(target) || desktopButton?.contains(target) || mobileButton?.contains(target),
  );
}

export function useHashRoute() {
  const read = React.useCallback(() => {
    const hash = window.location.hash.replace(/^#/, "");
    return hash.startsWith("/") ? hash : "/brain";
  }, []);
  const [route, setRoute] = React.useState(read);

  React.useEffect(() => {
    const onHashChange = () => setRoute(read());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [read]);

  return route;
}

export function useBrainState() {
  const [state, setState] = React.useState<BrainState>("idle");
  const [intensity, setIntensity] = React.useState(0.58);

  const setBrain = React.useCallback((next: BrainState, nextIntensity?: number) => {
    setState(next);
    if (nextIntensity !== undefined) setIntensity(clamp(nextIntensity, 0.38, 1));
  }, []);

  return { state, intensity, setBrain };
}

/**
 * Synchronizes document theme/locale attributes, hash deep-link state, and
 * LivingBrain ambient state for the root application shell.
 */
export function useAppBootstrap() {
  const theme = useAppStore((state) => state.theme);
  const language = useAppStore((state) => state.language);
  const rawRoute = useHashRoute();
  const parsed = React.useMemo(() => parseHash(), [rawRoute]);
  const brain = useBrainState();

  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.lang = language === "ko" ? "ko" : "en";
  }, [theme, language]);

  return {
    theme,
    language,
    rawRoute,
    parsed,
    brainState: brain.state,
    intensity: brain.intensity,
    setBrain: brain.setBrain,
  };
}

/**
 * Manages the responsive shell popover menu, Escape/Tab focus trap, outside
 * pointer dismissal, and route-change auto-close.
 */
export function useShellMenu(activeRoute: string) {
  const [menuOpen, setMenuOpen] = React.useState(false);
  const menuPanelRef = React.useRef<HTMLDivElement>(null);
  const desktopMenuButtonRef = React.useRef<HTMLButtonElement>(null);
  const mobileMenuButtonRef = React.useRef<HTMLButtonElement>(null);
  const returnFocusRef = React.useRef<HTMLButtonElement | null>(null);

  const closeMenu = React.useCallback((restoreFocus = true) => {
    setMenuOpen(false);
    if (restoreFocus) {
      window.requestAnimationFrame(() => restoreFocusTo(returnFocusRef.current));
    }
  }, []);

  const toggleMenu = React.useCallback((trigger: HTMLButtonElement) => {
    returnFocusRef.current = trigger;
    setMenuOpen((open) => !open);
  }, []);

  React.useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu();
        return;
      }
      if (event.key === "Tab") {
        const focusable = focusablesIn(menuPanelRef.current);
        if (!focusable.length) return;
        const first = focusable[0]!;
        const last = focusable[focusable.length - 1]!;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        pointerDownHitsMenu(
          target,
          menuPanelRef.current,
          desktopMenuButtonRef.current,
          mobileMenuButtonRef.current,
        )
      ) {
        return;
      }
      closeMenu(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onPointerDown);
    };
  }, [menuOpen, closeMenu]);

  React.useEffect(() => {
    setMenuOpen(false);
  }, [activeRoute]);

  React.useEffect(() => {
    if (!menuOpen) return;
    window.requestAnimationFrame(() => {
      const rendered = focusablesIn(menuPanelRef.current);
      const firstNavigationItem = rendered.find((element) => element.closest(".brain-more-nav"));
      (firstNavigationItem || rendered[0])?.focus();
    });
  }, [menuOpen]);

  return {
    menuOpen,
    menuPanelRef,
    desktopMenuButtonRef,
    mobileMenuButtonRef,
    closeMenu,
    toggleMenu,
  };
}
