import { get } from "./base";

/**
 * GET /api/cloud/status — dual credential path (API key / local CLI OAuth).
 *
 * The endpoint may not exist yet. A 404 or a network error must present as
 * "not configured", never as a configured provider, and never throw.
 */
export type CloudProviderMode = "api_key" | "cli_oauth" | "none";

export type CloudProviderStatus = {
  configured: boolean;
  mode: CloudProviderMode | string;
  provider: string;
  model: string;
  detail?: string | null;
};

export const CLOUD_STATUS_UNCONFIGURED: CloudProviderStatus = {
  configured: false,
  mode: "none",
  provider: "",
  model: "",
  detail: null,
};

export function normalizeCloudStatus(value: unknown, ok = true): CloudProviderStatus {
  if (!ok || !value || typeof value !== "object" || Array.isArray(value)) {
    return { ...CLOUD_STATUS_UNCONFIGURED };
  }
  const record = value as Record<string, unknown>;
  const rawMode = typeof record.mode === "string" ? record.mode : "none";
  const mode: CloudProviderMode =
    rawMode === "api_key" || rawMode === "cli_oauth" ? rawMode : "none";
  const configured = Boolean(record.configured) && mode !== "none";
  if (!configured) {
    return {
      ...CLOUD_STATUS_UNCONFIGURED,
      detail: typeof record.detail === "string" && record.detail.trim()
        ? record.detail.trim()
        : null,
    };
  }
  return {
    configured: true,
    mode,
    provider: typeof record.provider === "string" ? record.provider : "",
    model: typeof record.model === "string" ? record.model : "",
    detail: typeof record.detail === "string" && record.detail.trim()
      ? record.detail.trim()
      : null,
  };
}

export function cloudStatus() {
  return get<CloudProviderStatus>("/api/cloud/status", CLOUD_STATUS_UNCONFIGURED);
}
