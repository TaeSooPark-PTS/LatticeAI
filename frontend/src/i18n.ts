import { COPY } from "./i18n/registry";
import "./i18n/shell";
import type { Language } from "./i18n/types";

declare const __APP_VERSION__: string;

export type { Language, TextMap } from "./i18n/types";
export { COPY, registerCopy } from "./i18n/registry";

/**
 * The build stamps `__APP_VERSION__` in via `define`; anything else that runs
 * this module (plain tsc output, a REPL) has no such identifier at all. The
 * thunk keeps that reference lazy, so the absent-identifier case stays a caught
 * ReferenceError — the same condition the old inline `typeof` guard answered
 * with "dev" — while a non-string injection still falls back the same way.
 */
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

/**
 * Interpolation values, plus the one reserved name that is not interpolated.
 *
 * `defaultValue` is what to show when the key is missing from both languages.
 * Callers were already passing it — `InstallScreen` asks for
 * `flow.install.stage.${stage}` with a human sentence as the default — but
 * until it was read here every unmapped stage printed the raw key
 * (`flow.install.stage.idle`) into the first-run install screen, and the
 * option itself leaked into the replacement loop as a `{defaultValue}` token.
 */
export type CopyValues = Record<string, string | number> & { defaultValue?: string };

export function t(language: Language, key: string, values?: CopyValues) {
  const { defaultValue, ...interpolations } = values || {};
  // `??`, not `||`: a copy entry that is deliberately the empty string is a
  // translation, not a miss, and must not fall through to the next language.
  const text0 = COPY[language]?.[key] ?? COPY.ko[key] ?? defaultValue ?? key;
  let text = text0;
  const replacements: Record<string, string | number> = { version: APP_VERSION, ...interpolations };
  for (const [name, value] of Object.entries(replacements)) {
    text = text.replaceAll(`{${name}}`, String(value));
  }
  return text;
}
