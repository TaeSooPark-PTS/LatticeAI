import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import * as React from "react";
import { vi } from "vitest";

import { latticeApi } from "@/api/client";
import { useAppStore } from "@/store/appStore";


export type ApiResult<T> = {
  ok: boolean;
  status: number;
  source: string;
  data: T;
  error?: string;
};

export function ok<T>(data: T): ApiResult<T> {
  return { ok: true, status: 200, source: "live", data };
}

export function fail<T>(error: string, data: T, status = 503): ApiResult<T> {
  return { ok: false, status, source: "unavailable", data, error };
}

export function stubApi(overrides: Partial<Record<keyof typeof latticeApi, unknown>> = {}) {
  for (const key of Object.keys(latticeApi) as Array<keyof typeof latticeApi>) {
    const value = latticeApi[key];
    if (typeof value !== "function") continue;
    const override = overrides[key];
    const spy = vi.spyOn(latticeApi, key as never) as unknown as {
      mockImplementation: (fn: (...args: unknown[]) => unknown) => void;
    };
    if (override !== undefined) {
      spy.mockImplementation(
        typeof override === "function"
          ? (override as (...args: unknown[]) => unknown)
          : () => Promise.resolve(override),
      );
    } else {
      spy.mockImplementation(() => Promise.resolve(ok({})));
    }
  }
  return latticeApi;
}

export type RenderPageOptions = {
  language?: "ko" | "en";
  mode?: "basic" | "advanced" | "admin";
  api?: Partial<Record<keyof typeof latticeApi, unknown>>;
};

export function renderPage(
  ui: React.ReactElement,
  { language = "ko", mode = "advanced", api = {} }: RenderPageOptions = {},
): RenderResult & { client: QueryClient } {
  stubApi(api);
  useAppStore.setState({ language, mode } as never);

  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  const result = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return Object.assign(result, { client });
}
