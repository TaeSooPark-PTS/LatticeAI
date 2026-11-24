import { isRecord as isRecordValue } from "@/lib/utils";
import type { ApiRecord } from "./types";

export const isRecord = isRecordValue as (value: unknown) => value is ApiRecord;

export function textValue(record: ApiRecord, keys: string[], fallback = "") {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return fallback;
}

export function titleValue(record: ApiRecord, keys: string[], fallback = "") {
  const value = textValue(record, keys, fallback);
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

// Parse a created/added timestamp into unix epoch milliseconds. Accepts ISO 8601
// strings, unix seconds, or unix milliseconds. Returns undefined when absent or
// unparseable so the time-exploration UI can fall back gracefully.
export function timestampValue(record: ApiRecord, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      // Heuristic: values below ~1e12 are seconds, otherwise milliseconds.
      return value < 1e12 ? value * 1000 : value;
    }
    if (typeof value === "string" && value.trim()) {
      const numeric = Number(value);
      if (Number.isFinite(numeric) && /^\d+$/.test(value.trim())) {
        return numeric < 1e12 ? numeric * 1000 : numeric;
      }
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return undefined;
}

export function stringArrayValue(record: ApiRecord, keys: string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
    }
  }
  return [];
}

// An evidence thumbnail must be self-contained. Anything that is not an
// inline `data:image/...` URI is dropped rather than rendered, so a payload
// can never turn a citation card into an outbound fetch.
export function dataImageValue(record: ApiRecord): string {
  const value = record["thumbnail"];
  return typeof value === "string" && value.startsWith("data:image/") ? value : "";
}

// Prefer the backend's confidence band; fall back to the same score bands the
// backend uses so older responses still explain themselves.
export function confidenceValue(record: ApiRecord, score: number): "high" | "medium" | "low" {
  const value = record["confidence"];
  if (value === "high" || value === "medium" || value === "low") return value;
  return score >= 0.65 ? "high" : score >= 0.3 ? "medium" : "low";
}

export function numberValue(record: ApiRecord, keys: string[]) {
  for (const key of keys) {
    const value = Number(record[key]);
    if (Number.isFinite(value)) return value;
  }
  return 0;
}

export function booleanValue(record: ApiRecord, keys: string[], fallback: boolean) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "boolean") return value;
  }
  return fallback;
}
