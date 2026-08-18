import { t, type Language } from "@/i18n";
import { titleize } from "@/lib/utils";
import { isRecord } from "./graphExplorer";
import type { GraphGroup, GraphNode } from "./graphExplorer";

/**
 * The selected node's group, by name. `groups` is `ExplorerModel.groups` —
 * every id any node was assigned to during parsing, `node.group` included —
 * so the lookup miss this defends against cannot happen for a real node.
 */
export function groupLabelFor(node: GraphNode, groups: GraphGroup[]): string {
  const found = groups.find((group) => group.id === node.group);
  /* v8 ignore next -- unreachable: see the function doc comment above. Kept
     as defense-in-depth. */
  return found?.label || node.group;
}

/** Graph node classes are schema words; show the reader's language. */
export function graphTypeLabel(type: string, language: Language) {
  /* v8 ignore next -- unreachable: both call sites pass a `GraphNode.type`,
     which `parseGraph`'s `field()` helper always resolves to a non-empty
     string (falling back to the literal "Node"). Kept as defense-in-depth. */
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
