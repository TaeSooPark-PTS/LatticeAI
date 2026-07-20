import * as React from "react";
import { Download, FileCheck2, Save } from "lucide-react";

import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import type { MessageFile } from "./types";

// Renders assistant text with fenced code blocks as readable code cards.
// Every code block gets copy + save-as-real-file actions so "show me code"
// answers can always become an actual file on disk with one click.
// Plain segments render lightweight Markdown (headings, bold, lists, links)
// so answers stop showing raw ** and * markers to the user.
export function MessageBody({ language, content }: { language: Language; content: string }) {
  const segments = React.useMemo(() => splitCodeSegments(content), [content]);
  return (
    <>
      {segments.map((segment, index) =>
        segment.kind === "code" ? (
          <CodeBlock key={index} language={language} code={segment.value} lang={segment.lang} />
        ) : segment.value.trim() ? (
          <MarkdownText key={index} value={segment.value} />
        ) : null,
      )}
    </>
  );
}

type MarkdownBlock =
  | { kind: "heading"; depth: number; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "paragraph"; lines: string[] };

function parseMarkdownBlocks(text: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) {
      blocks.push({ kind: "paragraph", lines: paragraph });
      paragraph = [];
    }
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) {
      flush();
      continue;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: "heading", depth: heading[1].length, text: heading[2] });
      continue;
    }
    const bullet = /^[-*•]\s+(.+)$/.exec(line);
    if (bullet) {
      flush();
      const last = blocks[blocks.length - 1];
      if (last?.kind === "list" && !last.ordered) last.items.push(bullet[1]);
      else blocks.push({ kind: "list", ordered: false, items: [bullet[1]] });
      continue;
    }
    const numbered = /^\d+[.)]\s+(.+)$/.exec(line);
    if (numbered) {
      flush();
      const last = blocks[blocks.length - 1];
      if (last?.kind === "list" && last.ordered) last.items.push(numbered[1]);
      else blocks.push({ kind: "list", ordered: true, items: [numbered[1]] });
      continue;
    }
    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      flush();
      const last = blocks[blocks.length - 1];
      if (last?.kind === "quote") last.lines.push(quote[1]);
      else blocks.push({ kind: "quote", lines: [quote[1]] });
      continue;
    }
    if (/^([-_*])\1{2,}$/.test(line)) {
      flush();
      continue;
    }
    paragraph.push(line);
  }
  flush();
  return blocks;
}

// Inline markdown: **bold**, `code`, and [label](https://...) links only.
// Italic is intentionally skipped — single asterisks are too ambiguous in
// model output to risk mangling ordinary text.
function renderInlineMarkdown(text: string): React.ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g;
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={key++}>{token.slice(1, -1)}</code>);
    } else {
      const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(token);
      if (link) {
        nodes.push(
          <a key={key++} href={link[2]} target="_blank" rel="noreferrer noopener">
            {link[1]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

function MarkdownText({ value }: { value: string }) {
  const blocks = React.useMemo(() => parseMarkdownBlocks(value), [value]);
  return (
    <div className="brain-md">
      {blocks.map((block, index) => {
        if (block.kind === "heading") {
          return (
            <p key={index} className={`brain-md-heading is-depth-${Math.min(block.depth, 3)}`}>
              {renderInlineMarkdown(block.text)}
            </p>
          );
        }
        if (block.kind === "list") {
          const items = block.items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInlineMarkdown(item)}</li>
          ));
          return block.ordered ? <ol key={index}>{items}</ol> : <ul key={index}>{items}</ul>;
        }
        if (block.kind === "quote") {
          return <blockquote key={index}>{renderInlineMarkdown(block.lines.join(" "))}</blockquote>;
        }
        return (
          <p key={index}>
            {block.lines.map((line, lineIndex) => (
              <React.Fragment key={lineIndex}>
                {lineIndex > 0 ? <br /> : null}
                {renderInlineMarkdown(line)}
              </React.Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}

type MessageSegment = { kind: "text" | "code"; value: string; lang?: string };

function splitCodeSegments(content: string): MessageSegment[] {
  const segments: MessageSegment[] = [];
  const fence = /```([\w.+-]*)\n([\s\S]*?)(?:```|$)/g;
  let cursor = 0;
  for (let match = fence.exec(content); match; match = fence.exec(content)) {
    if (match.index > cursor) {
      segments.push({ kind: "text", value: content.slice(cursor, match.index) });
    }
    segments.push({ kind: "code", value: match[2].replace(/\n$/, ""), lang: match[1] || undefined });
    cursor = match.index + match[0].length;
  }
  if (cursor < content.length) {
    segments.push({ kind: "text", value: content.slice(cursor) });
  }
  return segments.length ? segments : [{ kind: "text", value: content }];
}

const CODE_LANG_EXTENSION: Record<string, string> = {
  python: "py", py: "py", javascript: "js", js: "js", typescript: "ts", ts: "ts",
  tsx: "tsx", jsx: "jsx", html: "html", css: "css", json: "json", markdown: "md",
  md: "md", bash: "sh", sh: "sh", shell: "sh", zsh: "sh", sql: "sql", yaml: "yml",
  yml: "yml", toml: "toml", csv: "csv", xml: "xml", text: "txt", txt: "txt",
};

function suggestedFileName(lang?: string): string {
  const extension = CODE_LANG_EXTENSION[(lang || "").toLowerCase()] || "txt";
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("") + "-" + [
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0"),
  ].join("");
  return `chat-${stamp}.${extension}`;
}

function CodeBlock({ language, code, lang }: { language: Language; code: string; lang?: string }) {
  const [copied, setCopied] = React.useState(false);
  const [saveState, setSaveState] = React.useState<"idle" | "saving" | "saved" | "error">("idle");
  const [savedFile, setSavedFile] = React.useState<MessageFile | null>(null);
  const [saveError, setSaveError] = React.useState("");

  async function copy() {
    try {
      await navigator.clipboard?.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {}
  }

  async function saveAsFile() {
    if (saveState === "saving") return;
    setSaveState("saving");
    setSaveError("");
    const name = suggestedFileName(lang);
    const result = await latticeApi.saveChatFile(name, code);
    if (!result.ok) {
      setSaveState("error");
      setSaveError(result.error || t(language, "common.error.unknown"));
      return;
    }
    const data = result.data as { path?: string; bytes?: number };
    const path = data.path || name;
    setSavedFile({ path, filename: path.split("/").pop() || name, bytes: data.bytes || 0 });
    setSaveState("saved");
  }

  return (
    <div className="brain-code-block">
      <div className="brain-code-head">
        <span className="brain-code-lang">{lang || "text"}</span>
        <div className="brain-code-actions">
          <button type="button" onClick={() => void copy()}>
            {copied ? t(language, "brain.code.copied") : t(language, "brain.code.copy")}
          </button>
          <button type="button" onClick={() => void saveAsFile()} disabled={saveState === "saving"}>
            <Save className="h-3 w-3" aria-hidden="true" />
            {saveState === "saving" ? t(language, "brain.code.saving") : t(language, "brain.code.save")}
          </button>
        </div>
      </div>
      <pre><code>{code}</code></pre>
      {saveState === "saved" && savedFile ? (
        <CreatedFilesCard language={language} files={[savedFile]} compact />
      ) : null}
      {saveState === "error" ? (
        <small className="brain-code-save-error" role="alert">
          {t(language, "brain.code.saveError", { reason: saveError })}
        </small>
      ) : null}
    </div>
  );
}

function formatFileSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Friendly confirmation card for files the assistant actually created,
// with a one-click download so non-technical users can find their file.
export function CreatedFilesCard({
  language,
  files,
  compact = false,
}: {
  language: Language;
  files: MessageFile[];
  compact?: boolean;
}) {
  const [downloading, setDownloading] = React.useState<string | null>(null);
  const [error, setError] = React.useState("");

  async function download(file: MessageFile) {
    if (downloading) return;
    setDownloading(file.path);
    setError("");
    const result = await latticeApi.downloadWorkspaceFile(file.path, file.filename);
    if (!result.ok) setError(result.error || t(language, "common.error.unknown"));
    setDownloading(null);
  }

  return (
    <section className={`brain-created-files ${compact ? "is-compact" : ""}`} aria-label={t(language, "brain.files.title")}>
      <div className="brain-created-files-head">
        <FileCheck2 className="h-4 w-4" aria-hidden="true" />
        <strong>{t(language, "brain.files.title")}</strong>
      </div>
      <ul>
        {files.map((file) => (
          <li key={file.path}>
            <span className="brain-created-file-name">{file.filename}</span>
            {file.bytes ? <small>{formatFileSize(file.bytes)}</small> : null}
            <button type="button" onClick={() => void download(file)} disabled={downloading === file.path}>
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              {downloading === file.path ? t(language, "brain.files.downloading") : t(language, "brain.files.download")}
            </button>
          </li>
        ))}
      </ul>
      <small className="brain-created-files-hint">{t(language, "brain.files.hint")}</small>
      {error ? (
        <small className="brain-created-files-error" role="alert">
          {t(language, "brain.files.downloadError", { reason: error })}
        </small>
      ) : null}
    </section>
  );
}
