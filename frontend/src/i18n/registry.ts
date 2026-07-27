import type { Language, NamespaceCopy, TextMap } from "./types";

/**
 * Mutable copy table shared by every namespace.
 *
 * Only the `shell` namespace is registered eagerly — it holds the app frame,
 * language switcher, and generic `ui.*` outcomes that render before any route
 * resolves. Route-scoped namespaces (`brain`, `workspace`, `onboarding`)
 * register themselves when their module is imported, which happens inside the
 * lazy chunk of the route that needs them. That keeps ~3,000 lines of copy off
 * the first-paint path.
 *
 * Registration always completes before the importing component renders: a lazy
 * route's module graph is fully evaluated before React resolves the chunk, and
 * a namespace module is a static import of that route. `t()` therefore never
 * observes a half-registered table for a route that is on screen.
 *
 * `scripts/check_i18n_namespace_coverage.mjs` proves this holds — it fails the
 * build if a module uses a key whose namespace its chunk does not import.
 */
export const COPY: Record<Language, TextMap> = { ko: {}, en: {} };

const registered = new Set<NamespaceCopy>();

export function registerCopy(copy: NamespaceCopy): void {
  if (registered.has(copy)) return;
  registered.add(copy);
  Object.assign(COPY.ko, copy.ko);
  Object.assign(COPY.en, copy.en);
}
