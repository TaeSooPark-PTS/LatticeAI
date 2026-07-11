import { brainCopy } from "./i18n/brain";
import { onboardingCopy } from "./i18n/onboarding";
import { shellCopy } from "./i18n/shell";
import { workspaceCopy } from "./i18n/workspace";
import type { Language, TextMap } from "./i18n/types";

declare const __APP_VERSION__: string;

export type { Language, TextMap } from "./i18n/types";

export const APP_VERSION = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "dev";

export const LANGUAGE_LABELS: Record<Language, string> = {
  ko: "한국어",
  en: "English",
};

export const COPY: Record<Language, TextMap> = {
  ko: { ...brainCopy.ko, ...onboardingCopy.ko, ...workspaceCopy.ko, ...shellCopy.ko },
  en: { ...brainCopy.en, ...onboardingCopy.en, ...workspaceCopy.en, ...shellCopy.en },
};

export function t(language: Language, key: string, values?: Record<string, string | number>) {
  let text = COPY[language]?.[key] || COPY.ko[key] || key;
  const replacements = { version: APP_VERSION, ...(values || {}) };
  for (const [name, value] of Object.entries(replacements)) {
    text = text.replaceAll(`{${name}}`, String(value));
  }
  return text;
}
