import { asArray, isRecord } from "@/lib/utils";
import type { ConversationSummary } from "../types";

export const LEGACY_CONVERSATION_ID = "legacy-previous-history";
export const SEARCH_GROUP_LIMIT = 30;

export type ConversationMatch = {
  id: string;
  title: string;
  snippet: string;
  matches: number;
  updatedAt: number;
};

function snippetAround(content: string, needle: string) {
  const flat = content.replace(/\s+/g, " ").trim();
  const at = flat.toLowerCase().indexOf(needle.toLowerCase());
  const start = Math.max(0, at - 40);
  const end = start + 120;
  return `${start > 0 ? "…" : ""}${flat.slice(start, end)}${end < flat.length ? "…" : ""}`;
}

export function buildSearchMatches(data: unknown, query: string, known: ConversationSummary[]): ConversationMatch[] {
  const record = isRecord(data) ? data : {};
  const byId = new Map(known.map((item) => [item.id, item]));
  return asArray<Record<string, unknown>>(record.results)
    .map((group) => {
      const raw = String(group.conversation_id ?? "");
      const id = raw === "legacy" ? LEGACY_CONVERSATION_ID : raw;
      const messages = asArray<Record<string, unknown>>(group.messages);
      const summary = byId.get(id);
      const latest = Math.max(0, ...messages.map((message) => Date.parse(String(message.timestamp)) || 0));
      return {
        id,
        title: summary?.title ?? String(group.title ?? id),
        snippet: snippetAround(String(messages[0]?.content ?? ""), query),
        matches: messages.length,
        updatedAt: summary?.updatedAt ?? latest,
      };
    })
    .filter((item) => item.id)
    .sort((left, right) => right.updatedAt - left.updatedAt);
}
