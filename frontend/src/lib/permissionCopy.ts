import { t, type Language } from "@/i18n";

type ModeOption = {
  id: string;
  label: string;
  label_ko?: string | null;
  summary?: string | null;
  summary_ko?: string | null;
  warning?: string | null;
  warning_ko?: string | null;
};

function byId(language: Language, id: string, suffix: string, serverText: string) {
  const key = `system.permission.mode.${id}${suffix}`;
  const localized = t(language, key);
  return localized === key ? serverText : localized;
}

function serverLabel(option: Pick<ModeOption, "label" | "label_ko">, language: Language) {
  return (language === "ko" ? option.label_ko || option.label : option.label) || "";
}

export function permissionModeLabel(option: ModeOption, language: Language): string {
  return byId(language, option.id, "", serverLabel(option, language));
}

export function permissionModeSummary(option: ModeOption, language: Language): string {
  const fallback = (language === "ko" ? option.summary_ko || option.summary : option.summary) || "";
  return byId(language, option.id, ".summary", fallback);
}

export function permissionModeWarning(option: ModeOption, language: Language): string {
  const fallback = (language === "ko" ? option.warning_ko || option.warning : option.warning) || "";
  return byId(language, option.id, ".warning", fallback);
}

export function activeModeLabel(
  data: { mode?: string; label?: string; label_ko?: string | null } | undefined,
  language: Language,
): string {
  if (!data) return "";
  return byId(language, String(data.mode || ""), "", serverLabel({ label: data.label || "", label_ko: data.label_ko }, language));
}
