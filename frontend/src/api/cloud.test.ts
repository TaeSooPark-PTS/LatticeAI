import { afterEach, describe, expect, it } from "vitest";

import { jsonResponse, recordFetch, resetDispatcher } from "@/test/apiClientHarness";
import { CLOUD_STATUS_UNCONFIGURED, cloudStatus, normalizeCloudStatus } from "./cloud";

describe("normalizeCloudStatus", () => {
  it("treats a failed read as not configured, never as a live provider", () => {
    expect(normalizeCloudStatus({ configured: true, mode: "api_key", provider: "x", model: "y" }, false))
      .toEqual(CLOUD_STATUS_UNCONFIGURED);
    expect(normalizeCloudStatus(undefined, false)).toEqual(CLOUD_STATUS_UNCONFIGURED);
    expect(normalizeCloudStatus(null, true)).toEqual(CLOUD_STATUS_UNCONFIGURED);
    expect(normalizeCloudStatus(["api_key"], true)).toEqual(CLOUD_STATUS_UNCONFIGURED);
  });

  it("treats mode none or an unknown mode as not configured even when configured is true", () => {
    expect(normalizeCloudStatus({ configured: true, mode: "none", provider: "x", model: "y" }))
      .toMatchObject({ configured: false, mode: "none", provider: "", model: "" });
    expect(normalizeCloudStatus({ configured: true, mode: "mystery", provider: "x", model: "y" }))
      .toMatchObject({ configured: false, mode: "none" });
    expect(normalizeCloudStatus({ configured: false, mode: "api_key", provider: "x", model: "y" }))
      .toMatchObject({ configured: false, mode: "none" });
    expect(normalizeCloudStatus({ configured: true, mode: 7, provider: "x" }))
      .toMatchObject({ configured: false, mode: "none" });
  });

  it("keeps an honest detail on the not-configured path", () => {
    expect(normalizeCloudStatus({ configured: false, mode: "none", detail: "  no key  " }))
      .toMatchObject({ configured: false, detail: "no key" });
    expect(normalizeCloudStatus({ configured: false, mode: "none", detail: "   " }))
      .toMatchObject({ configured: false, detail: null });
  });

  it("accepts an api_key or cli_oauth payload with optional identity fields", () => {
    expect(normalizeCloudStatus({
      configured: true, mode: "api_key", provider: "openai", model: "gpt-4o", detail: "live",
    })).toEqual({
      configured: true, mode: "api_key", provider: "openai", model: "gpt-4o", detail: "live",
    });
    expect(normalizeCloudStatus({
      configured: true, mode: "cli_oauth", provider: "Antigravity", model: "gemini-3.7-flash",
    })).toEqual({
      configured: true, mode: "cli_oauth", provider: "Antigravity", model: "gemini-3.7-flash", detail: null,
    });
    expect(normalizeCloudStatus({ configured: true, mode: "api_key" })).toEqual({
      configured: true, mode: "api_key", provider: "", model: "", detail: null,
    });
  });
});

describe("cloudStatus", () => {
  afterEach(resetDispatcher);

  it("reads GET /api/cloud/status and keeps a 404 as the unconfigured shape", async () => {
    const calls = recordFetch(() => jsonResponse({ detail: "missing" }, 404));
    const result = await cloudStatus();
    expect(calls[0].method).toBe("GET");
    expect(calls[0].url.pathname).toBe("/api/cloud/status");
    expect(result.ok).toBe(false);
    expect(result.status).toBe(404);
    expect(result.data).toEqual(CLOUD_STATUS_UNCONFIGURED);
  });
});
