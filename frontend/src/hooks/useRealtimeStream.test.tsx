import * as React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { useRealtimeStream } from "./useRealtimeStream";
import { useAppStore } from "@/store/appStore";

class MockEventSource {
  static instances: MockEventSource[] = [];
  static throwOnConstruct = false;

  url: string;
  withCredentials: boolean;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string, init?: { withCredentials?: boolean }) {
    if (MockEventSource.throwOnConstruct) {
      throw new Error("EventSource blocked");
    }
    this.url = url;
    this.withCredentials = Boolean(init?.withCredentials);
    MockEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }
}

function makeWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("useRealtimeStream", () => {
  const originalEventSource = window.EventSource;

  beforeEach(() => {
    vi.useFakeTimers();
    MockEventSource.instances = [];
    MockEventSource.throwOnConstruct = false;
    useAppStore.setState({ workspaceId: "personal" });
    Object.defineProperty(window, "EventSource", {
      configurable: true,
      writable: true,
      value: MockEventSource,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(window, "EventSource", {
      configurable: true,
      writable: true,
      value: originalEventSource,
    });
  });

  it("falls back to polling when disabled or EventSource is absent or throws", () => {
    const qc = new QueryClient();
    const { result: disabledResult } = renderHook(() => useRealtimeStream(false), {
      wrapper: makeWrapper(qc),
    });
    expect(disabledResult.current.transport).toBe("polling");
    expect(disabledResult.current.pollingInterval).toBe(8000);

    MockEventSource.throwOnConstruct = true;
    const { result: thrownResult } = renderHook(() => useRealtimeStream(true), {
      wrapper: makeWrapper(qc),
    });
    expect(thrownResult.current.transport).toBe("polling");

    Object.defineProperty(window, "EventSource", {
      configurable: true,
      writable: true,
      value: undefined,
    });
    const { result: absentResult } = renderHook(() => useRealtimeStream(true), {
      wrapper: makeWrapper(qc),
    });
    expect(absentResult.current.transport).toBe("polling");
  });

  it("streams SSE events into realtimeFeed cache, deduplicates seq, invalidates presence, and reconnects on error", () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    useAppStore.setState({ workspaceId: "" });

    const { result, unmount } = renderHook(() => useRealtimeStream(true), {
      wrapper: makeWrapper(qc),
    });

    const es = MockEventSource.instances[0]!;
    expect(es.url).toContain("/realtime/stream?live=1");
    expect(es.url).not.toContain("workspace_id=");
    expect(es.withCredentials).toBe(true);

    act(() => {
      es.onopen?.();
    });
    expect(result.current.transport).toBe("sse");
    expect(result.current.pollingInterval).toBe(false);

    // Empty data and malformed JSON are ignored safely
    act(() => {
      es.onmessage?.({ data: "" });
      es.onmessage?.({ data: "{not-json" });
      // Non-envelope cache state stays untouched
      es.onmessage?.({ data: JSON.stringify({ seq: 1, area: "brain", event_type: "note" }) });
    });

    // Seed non-ok envelope first, then valid ok envelope without events array
    qc.setQueryData(["realtimeFeed"], { ok: false });
    act(() => {
      es.onmessage?.({ data: JSON.stringify({ seq: 1, area: "brain", event_type: "note" }) });
    });

    qc.setQueryData(["realtimeFeed"], { ok: true, data: {} });
    act(() => {
      es.onmessage?.({ data: JSON.stringify({ seq: 10, area: "presence", event_type: "join" }) });
      // Duplicate seq is ignored
      es.onmessage?.({ data: JSON.stringify({ seq: 10, area: "presence", event_type: "join" }) });
      // Seq-less event still appends cleanly
      es.onmessage?.({ data: JSON.stringify({ area: "brain", event_type: "capture" }) });
    });

    const cached = qc.getQueryData<{ ok: boolean; data: { events: Record<string, unknown>[] } }>([
      "realtimeFeed",
    ]);
    expect(cached?.data.events).toHaveLength(2);
    expect(cached?.data.events[0]!.event_type).toBe("capture");
    expect(cached?.data.events[1]!.seq).toBe(10);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["presence"] });

    // Error triggers polling fallback and scheduled reconnect
    act(() => {
      es.onerror?.();
    });
    expect(es.closed).toBe(true);
    expect(result.current.transport).toBe("polling");
    expect(result.current.pollingInterval).toBe(8000);

    act(() => {
      vi.advanceTimersByTime(1600);
    });
    expect(MockEventSource.instances).toHaveLength(2);

    // Trigger error again so cleanup clears pending reconnect timer cleanly
    act(() => {
      MockEventSource.instances[1]!.onerror?.();
    });
    unmount();

    // Ensure post-unmount callbacks are no-ops
    act(() => {
      es.onopen?.();
      es.onmessage?.({ data: JSON.stringify({ seq: 99 }) });
      es.onerror?.();
    });
  });
});
