import type { SearchFilterBody } from "@/api/client";

export type SearchFilterState = {
  since: string;
  until: string;
  includeSuperseded: boolean;
};


export const EMPTY_SEARCH_FILTERS: SearchFilterState = { since: "", until: "", includeSuperseded: false };

export function dayAfter(day: string): string {
  const next = new Date(`${day}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString().slice(0, 10);
}

export function isReversedRange(filters: SearchFilterState): boolean {
  return Boolean(filters.since && filters.until && filters.since > filters.until);
}

export function searchFilterBody(filters: SearchFilterState): SearchFilterBody | undefined {
  const body: SearchFilterBody = {
    ...(filters.since ? { since: filters.since } : {}),
    ...(filters.until ? { until: dayAfter(filters.until) } : {}),
    ...(filters.includeSuperseded ? { include_historical: true } : {}),
  };
  return Object.keys(body).length ? body : undefined;
}
