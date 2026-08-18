import type { PermissionModeOption, PermissionModeState } from "@/api/client";
import { t, type Language } from "@/i18n";
import { permissionModeLabel, permissionModeSummary } from "@/lib/permissionCopy";

/**
 * Capability flags the server documents on GET /api/permission-mode for the
 * three catalog ids. There is no dry-run endpoint: this is the same contract
 * the current-mode payload already carries, applied to the selected catalog
 * id so the UI can render a comparison before POST.
 *
 * Unknown ids return null — the preview then shows only catalog copy
 * (summary / risk / ack) and says so.
 */
export type ModeFlags = {
  proposal_first: boolean;
  workspace_writes_auto: boolean;
  knowledge_reads_auto: boolean;
  exec_auto: boolean;
  computer_observation_auto: boolean;
  computer_control_auto: boolean;
  circuit_breakers: boolean;
};

export const FLAG_KEYS = [
  "proposal_first",
  "workspace_writes_auto",
  "knowledge_reads_auto",
  "exec_auto",
  "computer_observation_auto",
  "computer_control_auto",
  "circuit_breakers",
] as const;

export type FlagKey = (typeof FLAG_KEYS)[number];

export function flagsForCatalogMode(id: string): ModeFlags | null {
  if (id === "strict") {
    return {
      proposal_first: true,
      workspace_writes_auto: false,
      knowledge_reads_auto: false,
      exec_auto: false,
      computer_observation_auto: false,
      computer_control_auto: false,
      circuit_breakers: true,
    };
  }
  if (id === "trusted") {
    return {
      proposal_first: false,
      workspace_writes_auto: true,
      knowledge_reads_auto: true,
      exec_auto: false,
      computer_observation_auto: true,
      computer_control_auto: false,
      circuit_breakers: true,
    };
  }
  if (id === "bypass") {
    return {
      proposal_first: false,
      workspace_writes_auto: true,
      knowledge_reads_auto: true,
      exec_auto: true,
      computer_observation_auto: true,
      computer_control_auto: true,
      circuit_breakers: true,
    };
  }
  return null;
}

export function flagsFromState(state: PermissionModeState): ModeFlags {
  return {
    proposal_first: Boolean(state.proposal_first),
    workspace_writes_auto: Boolean(state.workspace_writes_auto),
    knowledge_reads_auto: Boolean(state.knowledge_reads_auto),
    exec_auto: Boolean(state.exec_auto),
    computer_observation_auto: Boolean(state.computer_observation_auto),
    computer_control_auto: Boolean(state.computer_control_auto),
    circuit_breakers: state.circuit_breakers !== false,
  };
}

export type PreviewRow = {
  id: string;
  label: string;
  current: string;
  next: string;
  changed: boolean;
};

function yn(language: Language, on: boolean, kind: "auto" | "on"): string {
  if (kind === "auto") {
    return t(language, on ? "system.permission.preview.auto" : "system.permission.preview.ask");
  }
  return t(language, on ? "system.permission.preview.on" : "system.permission.preview.off");
}

function riskLabel(language: Language, risk: string): string {
  const key = `system.permission.risk.${risk}`;
  const label = t(language, key);
  return label === key ? risk : label;
}

export function buildPermissionPreview(
  language: Language,
  current: PermissionModeOption,
  draft: PermissionModeOption,
  currentFlags: ModeFlags,
): { rows: PreviewRow[]; fromCatalogOnly: boolean } {
  const draftFlags = flagsForCatalogMode(draft.id);
  const fromCatalogOnly = draftFlags === null;

  const rows: PreviewRow[] = [
    {
      id: "risk",
      label: t(language, "system.permission.preview.risk"),
      current: riskLabel(language, current.risk),
      next: riskLabel(language, draft.risk),
      changed: current.risk !== draft.risk,
    },
    {
      id: "ack",
      label: t(language, "system.permission.preview.ack"),
      current: t(language, current.requires_ack ? "system.permission.preview.ack.yes" : "system.permission.preview.ack.no"),
      next: t(language, draft.requires_ack ? "system.permission.preview.ack.yes" : "system.permission.preview.ack.no"),
      changed: Boolean(current.requires_ack) !== Boolean(draft.requires_ack),
    },
  ];

  if (draftFlags) {
    for (const key of FLAG_KEYS) {
      const currentOn = currentFlags[key];
      const nextOn = draftFlags[key];
      const kind: "auto" | "on" = key === "proposal_first" || key === "circuit_breakers" ? "on" : "auto";
      rows.push({
        id: key,
        label: t(language, `system.permission.preview.cap.${key}`),
        current: yn(language, currentOn, kind),
        next: yn(language, nextOn, kind),
        changed: currentOn !== nextOn,
      });
    }
  }

  return { rows, fromCatalogOnly };
}

export function previewModeCopy(option: PermissionModeOption, language: Language) {
  return {
    label: permissionModeLabel(option, language),
    summary: permissionModeSummary(option, language),
  };
}
