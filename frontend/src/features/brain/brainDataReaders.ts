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

export function timestampValue(record: ApiRecord, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
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

export function dataImageValue(record: ApiRecord): string {
  const value = record["thumbnail"];
  return typeof value === "string" && value.startsWith("data:image/") ? value : "";
}

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
