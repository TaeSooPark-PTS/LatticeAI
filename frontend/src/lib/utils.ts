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
