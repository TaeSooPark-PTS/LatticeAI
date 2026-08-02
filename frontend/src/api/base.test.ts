/**
 * Every API call in the product goes through this module, and its whole job is
 * to never throw: a request that fails must come back as a typed `ApiResult`
 * with `ok: false`, an empty-but-correctly-shaped `data`, and a message a
 * non-developer can read.
 *
 * Only `emptyFor` was covered. The error-translation half — the part that
 * decides whether a user sees "the local service is unreachable" or a raw
 * `TypeError: Failed to fetch` — had no test, and a regression there is silent:
 * the page still renders, the list is still empty, and nobody learns that the
 * sidecar was down rather than the Brain being empty.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { emptyFor, friendlyCaughtError, friendlyError, workspaceHeaders } from "./base";
import { useAppStore } from "@/store/appStore";

beforeEach(() => {
  useAppStore.setState({ workspaceId: null, language: "en", apiBase: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("emptyFor", () => {
  it("returns an empty array for list response shapes", () => {
    expect(emptyFor([{ id: "not-a-fallback" }])).toEqual([]);
  });

  it("preserves the declared object shape without returning the same object", () => {
    const shape = { items: [], total: 0, available: false };
    const fallback = emptyFor(shape);

    expect(fallback).toEqual(shape);
    expect(fallback).not.toBe(shape);
  });

  it("does not let a caller mutate the shared shape through the copy", () => {
    const shape = { items: [] as string[], total: 0 };
    const fallback = emptyFor(shape);
    fallback.total = 9;
    expect(shape.total).toBe(0);
  });

  it("preserves primitive empty shapes", () => {
    expect(emptyFor(0)).toBe(0);
    expect(emptyFor("")).toBe("");
    expect(emptyFor(null)).toBeNull();
    expect(emptyFor(false)).toBe(false);
  });
});

describe("workspaceHeaders", () => {
  it("sends no scope header when there is no active workspace", () => {
    expect(workspaceHeaders()).toEqual({});
  });

  it("sends the active workspace as X-Workspace-Id", () => {
    useAppStore.setState({ workspaceId: "ws-42" });
    expect(workspaceHeaders()).toEqual({ "X-Workspace-Id": "ws-42" });
  });
});

describe("friendlyError", () => {
  it("falls back when there is no error at all", () => {
    expect(friendlyError(null, "fallback")).toBe("fallback");
    expect(friendlyError(undefined, "fallback")).toBe("fallback");
  });

  it("prefers a plain string detail (FastAPI's default shape)", () => {
    expect(friendlyError({ detail: "Not permitted" }, "fallback")).toBe("Not permitted");
  });

  it.each([
    ["user_message", { user_message: "Approve it in the web app" }],
    ["reason", { reason: "file changed since staging" }],
    ["action", { action: "reindex" }],
    ["status", { status: "approval_expired" }],
  ])("reads %s out of a structured detail", (_label, detail) => {
    const message = friendlyError({ detail }, "fallback");
    expect(message).toBe(String(Object.values(detail)[0]));
  });

  it("prefers user_message over the other structured keys", () => {
    const message = friendlyError(
      {
        detail: {
          status: "conflict",
          reason: "stale",
          user_message: "Nothing was written",
        },
      },
      "fallback",
    );
    expect(message).toBe("Nothing was written");
  });

  it("falls back to a top-level message or error field", () => {
    expect(friendlyError({ message: "boom" }, "fallback")).toBe("boom");
    expect(friendlyError({ error: "nope" }, "fallback")).toBe("nope");
  });

  it("uses the fallback for a shape it does not recognise", () => {
    expect(friendlyError({ unexpected: 1 }, "fallback")).toBe("fallback");
    expect(friendlyError("a bare string", "fallback")).toBe("fallback");
  });
});

describe("friendlyCaughtError", () => {
  it.each([
    "The operation was aborted",
    "signal is aborted without reason",
    "Request timed out",
    "request timeout",
  ])("reports %s as a timeout, not a raw exception", (raw) => {
    const message = friendlyCaughtError(new Error(raw), "fallback");
    expect(message).not.toBe(raw);
    expect(message).not.toBe("fallback");
    expect(message.length).toBeGreaterThan(0);
  });

  it.each([
    "Failed to fetch",
    "Load failed",
    "NetworkError when attempting to fetch resource",
    "Network request failed",
  ])("reports %s as 'the local service is unreachable'", (raw) => {
    const message = friendlyCaughtError(new Error(raw), "fallback");
    expect(message).not.toBe(raw);
    expect(message).not.toBe("fallback");
    expect(message.length).toBeGreaterThan(0);
  });

  it("hides JSON parse noise behind the caller's fallback", () => {
    expect(friendlyCaughtError(new Error("Unexpected token < in JSON"), "fallback")).toBe(
      "fallback",
    );
    expect(friendlyCaughtError(new Error("is not valid JSON"), "fallback")).toBe(
      "fallback",
    );
  });

  it("passes an unrecognised message through rather than inventing one", () => {
    expect(friendlyCaughtError(new Error("disk on fire"), "fallback")).toBe("disk on fire");
  });

  it("stringifies a non-Error throw", () => {
    expect(friendlyCaughtError("plain string throw", "fallback")).toBe(
      "plain string throw",
    );
  });

  it("uses the fallback for an empty message", () => {
    expect(friendlyCaughtError(new Error(""), "fallback")).toBe("fallback");
  });

  it("localises the timeout message with the stored UI language", () => {
    useAppStore.setState({ language: "en" });
    const english = friendlyCaughtError(new Error("aborted"), "fallback");
    useAppStore.setState({ language: "ko" });
    const korean = friendlyCaughtError(new Error("aborted"), "fallback");
    expect(korean).not.toBe(english);
  });
});
