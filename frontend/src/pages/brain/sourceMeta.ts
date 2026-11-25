import { t, type Language } from "@/i18n";
import { titleize } from "@/lib/utils";
import { isRecord } from "./graphExplorer";
import type { GraphGroup, GraphNode } from "./graphExplorer";

export function groupLabelFor(node: GraphNode, groups: GraphGroup[]): string {
  const found = groups.find((group) => group.id === node.group);
  return found?.label || node.group;
}

export function graphTypeLabel(type: string, language: Language) {
  const raw = String(type || "");
  const canonical = raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase();
  for (const candidate of [raw, canonical]) {
    const key = `ui.entity.${candidate}`;
    const label = t(language, key);
    if (label !== key) return label;
  }
  return titleize(raw);
}

export function sourceType(item: Record<string, unknown>, language: Language) {
  const metadata = isRecord(item.metadata) ? item.metadata : {};
  const raw = String(item.source_type || item.type || item.kind || metadata.source_type || metadata.role || "").toLowerCase();
  if (/chat|conversation|message/.test(raw)) return t(language, "brain.sources.type.chat");
  if (/document|upload|file|pdf|markdown|text/.test(raw)) return t(language, "brain.sources.type.document");
  if (/import|archive|restore/.test(raw)) return t(language, "brain.sources.type.import");
  if (/manual|note/.test(raw)) return t(language, "brain.sources.type.manual");
  return t(language, "brain.sources.type.unknown");
}

export function sourceCreatedAt(item: Record<string, unknown>) {
  const metadata = isRecord(item.metadata) ? item.metadata : {};
  const value = item.created_at || item.timestamp || item.updated_at || metadata.created_at || metadata.timestamp;
  return value ? String(value) : "";
}
