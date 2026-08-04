import { COPY } from "./i18n/registry";
import "./i18n/shell";
import type { Language } from "./i18n/types";

declare const __APP_VERSION__: string;

export type { Language, TextMap } from "./i18n/types";
export { COPY, registerCopy } from "./i18n/registry";

export const APP_VERSION = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "dev";

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
