/**
 * Pure surface-parity helpers for the VS Code extension (v9.9.6).
 *
 * `extension.ts` needs the `vscode` module, so nothing in it can be unit
 * tested outside the editor host. Every parity decision that is really just
 * data shaping — how a grounding verdict reads, how a pending change proposal
 * is labelled, how a finished agent run is summarized — lives here instead:
 * no `vscode` import, no I/O, deterministic in and out.
 *
 * SURFACE_PARITY rule 1 applies: these read the *same* sidecar payloads the
 * web app consumes (`/chat` grounding, `/api/proposals`, `/agent`
 * explanation). No VS Code-specific API, no re-derived verdicts.
 */

export type Grounded = {
  /** "supported" | "unsupported" | "no_context" — verbatim from the API. */
  status: string;
  /** Codicon-style marker for notifications/status text. */
  icon: string;
  /** Short human label, matching the web badge's meaning. */
  label: string;
  /** Cited source titles, when the API reported any. */
  sources: string[];
  reason: string;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Read the `/chat` grounding verdict into something a notification can show.
 *
 * Honest by construction: a missing/unknown verdict is reported as "unknown",
 * never as 근거 있음. The extension must not invent a badge the server did
 * not issue.
 */
export function groundingBadge(payload: unknown): Grounded {
  const grounding = asRecord(asRecord(payload).grounding);
  const status = text(grounding.status);
  const sources = asArray(grounding.cited)
    .map((entry) => text(asRecord(entry).title))
    .filter(Boolean);
  const reason = text(grounding.reason);
  if (status === "supported") {
    return { status, icon: "$(check)", label: "근거 있음 / grounded", sources, reason };
  }
  if (status === "unsupported" || status === "no_context") {
    return { status, icon: "$(warning)", label: "근거 없음 / not grounded", sources: [], reason };
  }
  return { status: status || "unknown", icon: "$(question)", label: "근거 확인 불가 / unknown", sources: [], reason };
}

/** One-line grounding suffix for an answer notification. */
export function groundingLine(payload: unknown): string {
  const badge = groundingBadge(payload);
  const parts = [`${badge.icon} ${badge.label}`];
  if (badge.sources.length) parts.push(badge.sources.slice(0, 3).join(", "));
  else if (badge.reason) parts.push(badge.reason);
  return parts.join(" — ");
}

export type ProposalSummary = {
  id: string;
  title: string;
  path: string;
  changeClass: string;
  createdAt: string;
};

/**
 * `GET /api/proposals` → the rows a Review Center quick pick renders.
 *
 * Tolerant of both the wrapped (`{items: [...]}`) and bare-list shapes, and
 * of items whose provenance carries the path under different keys. Rows
 * without an id are dropped — an un-actionable row is worse than no row.
 */
export function parseProposals(payload: unknown): ProposalSummary[] {
  const root = asRecord(payload);
  const rows = Array.isArray(payload) ? payload : asArray(root.items);
  return rows.flatMap((raw): ProposalSummary[] => {
    const item = asRecord(raw);
    const id = text(item.id);
    if (!id) return [];
    const body = asRecord(item.payload);
    const provenance = asRecord(item.provenance);
    const path =
      text(body.path) || text(provenance.path) || text(body.target) || "";
    return [{
      id,
      title: text(item.title) || path || id,
      path,
      changeClass:
        text(body.change_class) || text(provenance.change_class) || "",
      createdAt: text(item.created_at) || text(item.createdAt),
    }];
  });
}

export type RunSummary = {
  status: string;
  finalState: string;
  ok: boolean;
  headline: string;
  details: string[];
  files: string[];
  steps: string[];
};

const STEP_MARKERS: Record<string, string> = {
  planned: "$(list-ordered)",
  tool: "$(play)",
  proposed: "$(git-pull-request)",
  blocked: "$(circle-slash)",
  parse_error: "$(warning)",
  verdict: "$(verified)",
  final: "$(check)",
  state: "$(circle-outline)",
  rolled_back: "$(discard)",
};

/**
 * Reduce an `/agent` response into a readable run summary.
 *
 * `ok` follows the server's own verdict (`explanation.ok`, else
 * `final_state === "DONE"`): the extension never upgrades NEEDS_REVIEW into
 * success. `headline`/`details` come from the server's plain-language
 * explanation so VS Code and the web app say the same thing about the same
 * run — including how much a weak model struggled.
 */
export function summarizeRun(payload: unknown, language: "ko" | "en" = "ko"): RunSummary {
  const root = asRecord(payload);
  const explanation = asRecord(root.explanation);
  const localized = (entry: unknown): string => text(asRecord(entry)[language]);
  const finalState = text(root.final_state);
  const files = asArray(root.created_files)
    .map((entry) => (typeof entry === "string" ? entry : text(asRecord(entry).path)))
    .filter(Boolean);
  const steps = asArray(root.steps).flatMap((raw): string[] => {
    const step = asRecord(raw);
    if (step.state !== "EXECUTING") return [];
    const action = text(step.action);
    if (!action || action === "parse_error") return [];
    const failed = typeof step.error === "string" && step.error.length > 0;
    const proposed = Boolean(asRecord(step.result).proposed);
    const marker = proposed
      ? STEP_MARKERS.proposed
      : failed
        ? STEP_MARKERS.blocked
        : STEP_MARKERS.tool;
    const target = text(asRecord(step.args).path);
    return [`${marker} ${action}${target ? ` — ${target}` : ""}`];
  });
  return {
    status: text(root.status),
    finalState,
    ok: explanation.ok === true || (explanation.ok === undefined && finalState === "DONE"),
    headline: localized(explanation.headline),
    details: asArray(explanation.details).map(localized).filter(Boolean),
    files,
    steps,
  };
}

/** Multi-line run report for `showInformationMessage` / the output channel. */
export function runReport(payload: unknown, language: "ko" | "en" = "ko"): string {
  const summary = summarizeRun(payload, language);
  const lines: string[] = [];
  const state = summary.finalState || summary.status || "unknown";
  lines.push(`${summary.ok ? "$(check)" : "$(warning)"} ${state}`);
  if (summary.headline) lines.push(summary.headline);
  for (const step of summary.steps.slice(0, 8)) lines.push(`  ${step}`);
  if (summary.steps.length > 8) lines.push(`  … +${summary.steps.length - 8}`);
  for (const detail of summary.details) lines.push(`• ${detail}`);
  if (summary.files.length) lines.push(`$(file) ${summary.files.join(", ")}`);
  return lines.join("\n");
}
