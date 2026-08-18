import { describe, expect, it } from "vitest";

import type { PermissionModeOption, PermissionModeState } from "@/api/client";
import {
  buildPermissionPreview,
  flagsForCatalogMode,
  flagsFromState,
  previewModeCopy,
} from "./permissionModePreview";

const option = (id: string, extra: Partial<PermissionModeOption> = {}): PermissionModeOption => ({
  id,
  label: id,
  label_ko: id,
  summary: `${id} summary`,
  summary_ko: `${id} 요약`,
  risk: "low",
  requires_ack: false,
  ...extra,
});

describe("flagsForCatalogMode", () => {
  it("mirrors the server contract for the three catalog ids", () => {
    expect(flagsForCatalogMode("strict")).toMatchObject({
      proposal_first: true,
      workspace_writes_auto: false,
      exec_auto: false,
      circuit_breakers: true,
    });
    expect(flagsForCatalogMode("trusted")).toMatchObject({
      proposal_first: false,
      workspace_writes_auto: true,
      knowledge_reads_auto: true,
      exec_auto: false,
      computer_observation_auto: true,
      computer_control_auto: false,
    });
    expect(flagsForCatalogMode("bypass")).toMatchObject({
      exec_auto: true,
      computer_control_auto: true,
      circuit_breakers: true,
    });
  });

  it("refuses to invent flags for a mode the catalog grew after this client", () => {
    expect(flagsForCatalogMode("supervised")).toBeNull();
  });
});

describe("flagsFromState", () => {
  it("reads the live payload and treats a missing breaker as still on", () => {
    const state = {
      mode: "trusted",
      proposal_first: false,
      workspace_writes_auto: true,
      knowledge_reads_auto: true,
      exec_auto: false,
      computer_observation_auto: true,
      computer_control_auto: false,
    } as PermissionModeState;
    expect(flagsFromState(state).circuit_breakers).toBe(true);
    expect(flagsFromState({ ...state, circuit_breakers: false }).circuit_breakers).toBe(false);
  });
});

describe("buildPermissionPreview", () => {
  it("diffs catalog fields and inferred flags between strict and trusted", () => {
    const current = option("strict");
    const draft = option("trusted", { risk: "medium" });
    const { rows, fromCatalogOnly } = buildPermissionPreview(
      "ko",
      current,
      draft,
      flagsForCatalogMode("strict")!,
    );

    expect(fromCatalogOnly).toBe(false);
    const risk = rows.find((row) => row.id === "risk");
    expect(risk?.changed).toBe(true);
    expect(risk?.current).toBe("낮음");
    expect(risk?.next).toBe("보통");

    const writes = rows.find((row) => row.id === "workspace_writes_auto");
    expect(writes?.changed).toBe(true);
    expect(writes?.current).toBe("물어봄");
    expect(writes?.next).toBe("알아서");

    const breakers = rows.find((row) => row.id === "circuit_breakers");
    expect(breakers?.changed).toBe(false);
  });

  it("names an acknowledgement that is already in force on the current mode", () => {
    const { rows } = buildPermissionPreview(
      "ko",
      option("bypass", { risk: "high", requires_ack: true }),
      option("trusted", { risk: "medium" }),
      flagsForCatalogMode("bypass")!,
    );
    const ack = rows.find((row) => row.id === "ack");
    expect(ack?.current).toBe("필요함");
    expect(ack?.next).toBe("없음");
    expect(ack?.changed).toBe(true);
  });

  it("keeps only catalog rows when the draft id is unknown", () => {
    const { rows, fromCatalogOnly } = buildPermissionPreview(
      "en",
      option("strict"),
      option("supervised", { risk: "experimental", requires_ack: true }),
      flagsForCatalogMode("strict")!,
    );

    expect(fromCatalogOnly).toBe(true);
    expect(rows.map((row) => row.id)).toEqual(["risk", "ack"]);
    expect(rows.find((row) => row.id === "ack")?.next).toBe("Required");
    expect(rows.find((row) => row.id === "risk")?.next).toBe("experimental");
  });
});

describe("previewModeCopy", () => {
  it("uses this app's words for a known mode", () => {
    const copy = previewModeCopy(option("strict", { label: "Strict", summary: "server" }), "ko");
    expect(copy.label).toBe("먼저 물어보기");
    expect(copy.summary).toContain("확인받습니다");
  });
});
