import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiBase } from "@/api/base";
import { useAppStore } from "@/store/appStore";

export type RealtimeTransportState = "sse" | "polling";

/**
 * Subscribes to `GET /realtime/stream?live=1` via `EventSource` for sub-second
 * feed and presence updates, falling back automatically to React Query polling
 * when `EventSource` is unavailable or disconnects.
 */
export function useRealtimeStream(enabled = true) {
  const queryClient = useQueryClient();
  const workspaceId = useAppStore((state) => state.workspaceId);
  const [transport, setTransport] = React.useState<RealtimeTransportState>("polling");

  React.useEffect(() => {
    if (!enabled || typeof window === "undefined" || typeof window.EventSource !== "function") {
      setTransport("polling");
      return;
    }

    let disposed = false;
    let reconnectTimer: number | null = null;
    let source: EventSource | null = null;
    let attempts = 0;

    const connect = () => {
      const params = new URLSearchParams({ live: "1" });
      if (workspaceId) params.set("workspace_id", workspaceId);
      const url = `${apiBase()}/realtime/stream?${params.toString()}`;
      try {
        source = new window.EventSource(url, { withCredentials: true });
      } catch {
        setTransport("polling");
        return;
      }

      source.onopen = () => {
        if (disposed) return;
        attempts = 0;
        setTransport("sse");
      };

      source.onmessage = (message) => {
        if (disposed || !message.data) return;
        try {
          const parsed = JSON.parse(message.data) as Record<string, unknown>;
          queryClient.setQueryData(["realtimeFeed"], (prev: unknown) => {
            if (!prev || typeof prev !== "object") return prev;
            const envelope = prev as { ok?: boolean; data?: Record<string, unknown> };
            if (!envelope.ok || !envelope.data) return prev;
            const current = Array.isArray(envelope.data.events)
              ? (envelope.data.events as Record<string, unknown>[])
              : [];
            const seq = parsed.seq;
            if (seq !== undefined && current.some((item) => item.seq === seq)) {
              return prev;
            }
            return {
              ...envelope,
              data: {
                ...envelope.data,
                events: [parsed, ...current].slice(0, 80),
              },
            };
          });
          if (parsed.area === "presence") {
            void queryClient.invalidateQueries({ queryKey: ["presence"] });
          }
        } catch {
          // Malformed frames are ignored; keepalive comments never reach onmessage.
        }
      };

      source.onerror = () => {
        if (disposed) return;
        source?.close();
        source = null;
        setTransport("polling");
        const delayMs = Math.min(15000, 1500 * Math.pow(2, Math.min(attempts, 3)));
        attempts += 1;
        reconnectTimer = window.setTimeout(connect, delayMs);
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      source?.close();
    };
  }, [enabled, queryClient, workspaceId]);

  return {
    transport,
    pollingInterval: transport === "sse" ? (false as const) : 8000,
  };
}
