import type { Language, NamespaceCopy, TextMap } from "./types";

export const COPY: Record<Language, TextMap> = { ko: {}, en: {} };

const registered = new Set<NamespaceCopy>();

export function registerCopy(copy: NamespaceCopy): void {
  if (registered.has(copy)) return;
  registered.add(copy);
  Object.assign(COPY.ko, copy.ko);
  Object.assign(COPY.en, copy.en);
}
