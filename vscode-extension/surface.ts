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

/**
 * One live `agent_step` frame as an output-channel line (v9.9.7).
 *
 * The web timeline renders the same frames; this is the editor's rendering of
 * the identical payload — no VS Code-specific event shape, no re-derived
 * verdict. Unknown events degrade to a neutral marker rather than vanishing,
 * so a future backend addition is still visible.
 */
export function stepLine(step: unknown): string {
  const frame = asRecord(step);
  const phase = text(frame.phase) || "run";
  const event = text(frame.event) || "step";
  const marker =
    event === "blocked" || event === "parse_error"
      ? STEP_MARKERS.blocked
      : frame.ok === false
        ? STEP_MARKERS.blocked
        : STEP_MARKERS[event] ?? "$(circle-outline)";
  const parts: string[] = [`${marker} ${phase}/${event}`];
  for (const key of ["action", "path", "decision", "verdict", "state", "reason"]) {
    const value = text(frame[key]);
    if (value) parts.push(`${key}=${value}`);
  }
  if (typeof frame.steps === "number") parts.push(`steps=${frame.steps}`);
  if (frame.ok === false) parts.push("failed");
  return parts.join(" ");
}

export type EvidenceAction = {
  id: string;
  kind: string;
  label: string;
  prompt: string;
  suggestedPath: string;
};

/**
 * `POST /api/evidence/actions` → the rows an editor quick pick renders.
 *
 * Actions without a prompt are dropped: an action the editor cannot actually
 * send is worse than no action. Labels come from the server's localized pair,
 * so VS Code and the web app offer the same wording.
 */
export function parseEvidenceActions(payload: unknown, language: "ko" | "en" = "ko"): EvidenceAction[] {
  const root = asRecord(payload);
  return asArray(root.actions).flatMap((raw): EvidenceAction[] => {
    const action = asRecord(raw);
    const prompt = text(action.prompt);
    const id = text(action.id);
    if (!prompt || !id) return [];
    const label = asRecord(action.label);
    return [{
      id,
      kind: text(action.kind),
      label: text(label[language]) || text(label.en) || id,
      prompt,
      suggestedPath: text(action.suggested_path),
    }];
  });
}

/** Cited source ids from a `/chat` grounding verdict, for evidence actions. */
export function citedSourceIds(payload: unknown): string[] {
  const grounding = asRecord(asRecord(payload).grounding);
  const fromCited = asArray(grounding.cited)
    .map((entry) => text(asRecord(entry).id))
    .filter(Boolean);
  if (fromCited.length) return fromCited;
  return asArray(grounding.source_ids).map((entry) => text(entry)).filter(Boolean);
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

// ── Artifact cards (v10.4.0) ─────────────────────────────────────────────────
//
// The web app renders `artifacts[]` as cards: filename, size, whether the
// content was deterministically repaired, and whether the Brain has remembered
// it yet. The editor used to show only a flat list of created paths, so a
// repaired scaffold looked identical to clean model output — SURFACE_PARITY
// listed this as ◐. These helpers shape the *same* `/agent` payload; the
// editor renders them as a QuickPick instead of cards, which is a rendering
// difference, not a contract difference.

export type ArtifactCard = {
  /** Workspace-relative path exactly as the server reported it. */
  path: string;
  filename: string;
  bytes: number;
  previewable: boolean;
  /** True when ArtifactWritePipeline had to repair the model's content. */
  repaired: boolean;
  /** Server-side validity verdict; never upgraded by the extension. */
  valid: boolean;
  /** One-line label for a QuickPick row. */
  label: string;
  /** Honest sub-label: size plus any caveat the server reported. */
  detail: string;
};

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Parse `/agent`'s `artifacts[]` into editor-renderable cards.
 *
 * Falls back to `created_files` when a run predates the artifact contract, so
 * an older sidecar still lists what it wrote rather than showing nothing.
 */
export function parseArtifacts(
  payload: unknown,
  language: "ko" | "en" = "ko",
): ArtifactCard[] {
  const root = asRecord(payload);
  const raw = asArray(root.artifacts);
  const copy = {
    repaired: language === "ko" ? "자동 보정됨" : "auto-repaired",
    invalid: language === "ko" ? "검증 실패" : "failed validation",
    preview: language === "ko" ? "미리보기 가능" : "previewable",
  };

  const fromArtifacts = raw.flatMap((entry): ArtifactCard[] => {
    const item = asRecord(entry);
    const path = text(item.path);
    if (!path) return [];
    const bytes = typeof item.bytes === "number" ? item.bytes : 0;
    const repaired = item.repaired === true;
    const valid = item.valid !== false;
    const notes = [
      formatBytes(bytes),
      repaired ? copy.repaired : "",
      valid ? "" : copy.invalid,
      item.previewable === true ? copy.preview : "",
    ].filter(Boolean);
    return [{
      path,
      filename: text(item.filename) || path.split("/").pop() || path,
      bytes,
      previewable: item.previewable === true,
      repaired,
      valid,
      label: `${repaired ? "$(wand)" : valid ? "$(file)" : "$(warning)"} ${
        text(item.filename) || path
      }`,
      detail: notes.join(" · ") || path,
    }];
  });
  if (fromArtifacts.length) return fromArtifacts;

  // Legacy shape: created_files only, with no per-file honesty flags. Say so
  // rather than implying the file was verified.
  return asArray(root.created_files).flatMap((entry): ArtifactCard[] => {
    const path = typeof entry === "string" ? entry : text(asRecord(entry).path);
    if (!path) return [];
    return [{
      path,
      filename: path.split("/").pop() || path,
      bytes: 0,
      previewable: false,
      repaired: false,
      valid: true,
      label: `$(file) ${path}`,
      detail: language === "ko" ? "세부 정보 없음" : "no artifact detail reported",
    }];
  });
}

/** Compact artifact summary for the output channel. */
export function artifactReport(
  payload: unknown,
  language: "ko" | "en" = "ko",
): string {
  const cards = parseArtifacts(payload, language);
  if (!cards.length) {
    return language === "ko" ? "생성된 파일 없음" : "no files produced";
  }
  return cards.map((card) => `${card.label} — ${card.detail}`).join("\n");
}

// ── Model recommendation (v10.4.0) ───────────────────────────────────────────

export type ModelRecommendation = {
  modelId: string;
  runtime: string;
  /** Why this machine got this recommendation, verbatim from the server. */
  rationale: string[];
};

/**
 * Read the hardware-derived recommendation out of `GET /setup/scan`.
 *
 * The web Library view shows *why* a model is recommended for this machine.
 * The editor's picker listed models with no reasoning, which SURFACE_PARITY
 * recorded as ◐. Returns null when the server offers no recommendation — the
 * picker then shows its plain catalogue rather than inventing a reason.
 */
export function parseModelRecommendation(payload: unknown): ModelRecommendation | null {
  const zeroConfig = asRecord(asRecord(payload).zero_config);
  const recommend = asRecord(zeroConfig.recommend);
  const modelId = text(recommend.model_id);
  if (!modelId) return null;
  return {
    modelId,
    runtime: text(recommend.runtime),
    rationale: asArray(recommend.rationale).map(text).filter(Boolean),
  };
}
