import { t, type Language } from "@/i18n";
import { asArray, isRecord } from "@/lib/utils";

export function conversationMarkdown(language: Language, title: string, data: unknown): string | null {
  const record = isRecord(data) ? data : {};
  const blocks = asArray<Record<string, unknown>>(record.messages).flatMap((message) => {
    const { role, content, timestamp } = message;
    if ((role !== "user" && role !== "assistant") || typeof content !== "string" || !content.trim()) return [];
    const speaker = t(language, `brain.history.library.role.${role}`);
    const stamp = typeof timestamp === "string" && timestamp ? ` · ${timestamp}` : "";
    return [`## ${speaker}${stamp}\n\n${content.trim()}`];
  });
  return blocks.length ? `# ${title}\n\n${blocks.join("\n\n")}\n` : null;
}

export function markdownFileName(title: string): string {
  const safe = title.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").replace(/\s+/g, " ").replace(/^[. ]+|[. ]+$/g, "");
  return `${safe.slice(0, 80) || "conversation"}.md`;
}

export function downloadMarkdown(fileName: string, markdown: string) {
  const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
