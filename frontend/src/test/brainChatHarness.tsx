import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { vi } from "vitest";

import { useBrainChat } from "@/features/brain/hooks/useBrainChat";


export function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

export type BrainCall = { state: string; intensity?: number };

export function setup(overrides: Partial<Parameters<typeof useBrainChat>[0]> = {}) {
  const brainStates: BrainCall[] = [];
  const memoryFeedback: Array<string | null> = [];
  const options = {
    language: "ko" as const,
    modelReady: true,
    onBrainChange: (state: string, intensity?: number) => {
      brainStates.push({ state, intensity });
    },
    setMemoryFeedback: ((value: unknown) => {
      memoryFeedback.push(typeof value === "function" ? "(updater)" : (value as string | null));
    }) as never,
    announceSaved: (message: string) => {
      memoryFeedback.push(message);
    },
    beginIngestion: vi.fn(),
    completeIngestion: vi.fn().mockResolvedValue({ memories: 3, entities: 2 }),
    failIngestion: vi.fn(),
    setLastRecallQuery: vi.fn() as never,
    attachAnswerProof: vi.fn().mockResolvedValue(true),
    ...overrides,
  };
  const rendered = renderHook(() => useBrainChat(options as never), { wrapper });
  return { ...rendered, brainStates, memoryFeedback, options };
}
