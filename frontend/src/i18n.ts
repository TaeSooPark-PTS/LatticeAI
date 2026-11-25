import { COPY } from "./i18n/registry";
import "./i18n/shell";
import type { Language } from "./i18n/types";

declare const __APP_VERSION__: string;

export type { Language, TextMap } from "./i18n/types";
export { COPY, registerCopy } from "./i18n/registry";

export function resolveAppVersion(read: () => unknown): string {
  try {
    const value = read();
    return typeof value === "string" ? value : "dev";
  } catch {
    return "dev";
  }
}

export const APP_VERSION = resolveAppVersion(() => __APP_VERSION__);

export const LANGUAGE_LABELS: Record<Language, string> = {
  ko: "한국어",
  en: "English",
};

export type CopyValues = Record<string, string | number> & { defaultValue?: string };

export function t(language: Language, key: string, values?: CopyValues) {
  const { defaultValue, ...interpolations } = values || {};
  const text0 = COPY[language]?.[key] ?? COPY.ko[key] ?? defaultValue ?? key;
  let text = text0;
  const replacements: Record<string, string | number> = { version: APP_VERSION, ...interpolations };
  for (const [name, value] of Object.entries(replacements)) {
    text = text.replaceAll(`{${name}}`, String(value));
  }
  return text;
}
