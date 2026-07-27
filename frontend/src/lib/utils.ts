import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtNumber(value: unknown, fallback = "0") {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return new Intl.NumberFormat().format(n);
}

export function pct(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0%";
  return `${Math.round(n * 100)}%`;
}

export function shortId(value: unknown, length = 10) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length)}...` : text;
}

export function asArray<T = Record<string, unknown>>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export function titleize(value: unknown) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

/**
 * Read text that came from a document or a model as plain words.
 *
 * Titles and summaries carry whatever Markdown the source had, so search
 * results and graph nodes showed "**요약하자면,**" verbatim — which reads as a
 * rendering fault, not as emphasis. Returns "" for an absent value so callers
 * can fall through to the next candidate.
 */
export function plainText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  const text = String(value)
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(^|\s)[*_]([^*_\n]+)[*_](?=\s|$)/g, "$1$2")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  return text === "-" ? "" : text;
}

/**
 * "mlx-community/gemma-4-26b-a4b-it-4bit" is a package coordinate, not a name a
 * person recognises. Keep the words, drop the registry and the quantisation.
 */
export function humanizeModelId(id: string): string {
  const tail = id.split("/").pop() || id;
  const words = tail
    .replace(/[-_]/g, " ")
    .replace(/\b(4bit|8bit|6bit|bf16|fp16|gguf|mlx|q4[\w]*|k m)\b/gi, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!words) return id;
  return words
    .split(" ")
    .map((word) => (/^[a-z]/.test(word) ? word[0].toUpperCase() + word.slice(1) : word))
    .join(" ");
}
