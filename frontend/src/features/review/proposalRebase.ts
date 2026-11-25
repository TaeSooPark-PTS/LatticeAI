import { latticeApi } from "@/api/client";
import { isRecord } from "@/lib/utils";


export type RebaseOutcome = "rebased" | "already_applied";

const MAX_DIFF_LINES = 400;
const SMALL_TIER_DIFF_LINES = 40;
const MAX_DIFF_INPUT_LINES = 400;

function utf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

async function sha256Hex(text: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return "";
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function unifiedDiffLines(before: string, after: string, path: string): string[] {
  const header = [`--- a/${path}`, `+++ b/${path}`];
  const beforeLines = before.length ? before.split("\n") : [];
  const afterLines = after.length ? after.split("\n") : [];
  if (beforeLines.length > MAX_DIFF_INPUT_LINES || afterLines.length > MAX_DIFF_INPUT_LINES) {
    return [
      ...header,
      ...beforeLines.map((line) => `-${line}`),
      ...afterLines.map((line) => `+${line}`),
    ].slice(0, MAX_DIFF_LINES);
  }
  const rows = beforeLines.length;
  const cols = afterLines.length;
  const table: Uint32Array[] = Array.from({ length: rows + 1 }, () => new Uint32Array(cols + 1));
  for (let i = rows - 1; i >= 0; i -= 1) {
    const row = table[i]!;
    const nextRow = table[i + 1]!;
    for (let j = cols - 1; j >= 0; j -= 1) {
      row[j] = beforeLines[i] === afterLines[j]
        ? nextRow[j + 1]! + 1
        : Math.max(nextRow[j]!, row[j + 1]!);
    }
  }
  const body: string[] = [];
  let i = 0;
  let j = 0;
  while (i < rows && j < cols) {
    if (beforeLines[i] === afterLines[j]) {
      i += 1;
      j += 1;
    } else if (table[i + 1]![j]! >= table[i]![j + 1]!) {
      body.push(`-${beforeLines[i]}`);
      i += 1;
    } else {
      body.push(`+${afterLines[j]}`);
      j += 1;
    }
  }
  while (i < rows) body.push(`-${beforeLines[i++]}`);
  while (j < cols) body.push(`+${afterLines[j++]}`);
  if (!body.length) return [];
  return [...header, ...body].slice(0, MAX_DIFF_LINES);
}

export async function rebaseProposal(itemId: string): Promise<RebaseOutcome> {
  const detail = await latticeApi.proposalDetail(itemId);
  if (!detail.ok) throw new Error(detail.error || `HTTP ${detail.status}`);
  const item = detail.data as Record<string, unknown>;
  const payload = isRecord(item.payload) ? item.payload : {};
  const path = typeof payload.path === "string" ? payload.path : "";
  const kind = typeof item.kind === "string" && item.kind ? item.kind : "file_update";
  if (!path) throw new Error("proposal payload has no path");
  const staged = typeof payload.new_content === "string" ? payload.new_content : "";

  const current = await latticeApi.readWorkspaceFile(path);
  if (!current.ok && current.status !== 404) {
    throw new Error(current.error || `HTTP ${current.status}`);
  }
  const baseExists = current.ok;
  const currentContent = current.ok ? current.data.content : "";

  if (kind === "file_update" && baseExists && currentContent === staged) {
    await latticeApi.rejectProposal(itemId, "rebase: content already applied");
    return "already_applied";
  }
  if (kind === "file_delete" && !baseExists) {
    await latticeApi.rejectProposal(itemId, "rebase: file already deleted");
    return "already_applied";
  }

  const nextContent = kind === "file_delete" ? "" : staged;
  const diff = unifiedDiffLines(currentContent, nextContent, path);
  const baseSha = baseExists ? await sha256Hex(currentContent) : "";
  const created = await latticeApi.createReviewItem({
    title: typeof item.title === "string" && item.title ? item.title : path,
    summary: typeof item.summary === "string" ? item.summary : "",
    source: "change_proposal",
    kind,
    payload: {
      path,
      diff,
      ...(kind === "file_delete" ? {} : { new_content: staged }),
      tier: kind === "file_delete" ? "large" : diff.length <= SMALL_TIER_DIFF_LINES ? "small" : "large",
      before_bytes: utf8Bytes(currentContent),
      after_bytes: utf8Bytes(nextContent),
      ...(baseSha || !baseExists ? { base_exists: baseExists, base_sha256: baseSha } : {}),
    },
    provenance: {
      proposed_by: "user_rebase",
      reason: `rebased_from:${itemId}`,
      source_detail: "proposal_conflict_rebase",
    },
  });
  if (!created.ok) throw new Error(created.error || `HTTP ${created.status}`);
  const newId = String((created.data as Record<string, unknown>).id || "");
  await latticeApi.rejectProposal(itemId, newId ? `rebased_to:${newId}` : "rebased");
  return "rebased";
}
