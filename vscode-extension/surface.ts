
export type Grounded = {
  status: string;
  icon: string;
  label: string;
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

export function citedSourceIds(payload: unknown): string[] {
  const grounding = asRecord(asRecord(payload).grounding);
  const fromCited = asArray(grounding.cited)
    .map((entry) => text(asRecord(entry).id))
    .filter(Boolean);
  if (fromCited.length) return fromCited;
  return asArray(grounding.source_ids).map((entry) => text(entry)).filter(Boolean);
}

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


export type ArtifactCard = {
  path: string;
  filename: string;
  bytes: number;
  previewable: boolean;
  repaired: boolean;
  valid: boolean;
  label: string;
  detail: string;
};

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

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


export type ModelRecommendation = {
  modelId: string;
  runtime: string;
  rationale: string[];
};

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
