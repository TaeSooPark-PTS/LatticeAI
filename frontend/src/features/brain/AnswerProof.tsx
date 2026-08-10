import * as React from "react";
import { X } from "lucide-react";

import { latticeApi, type EvidenceAction } from "@/api/client";
import { t, type Language } from "@/i18n";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { isRecord, textValue } from "./brainData";
import type { Message } from "./types";

function citationDomId(messageId: string, citationId: string): string {
  return `${messageId}-cite-${citationId}`;
}

type Citation = NonNullable<Message["proof"]>["citations"][number];

// Inline, keyboard-focusable citation markers rendered next to the answer text.
// Activating one moves focus to the matching row in the evidence card below.
export function InlineCitationMarkers({
  language,
  proof,
  messageId,
}: {
  language: Language;
  proof: NonNullable<Message["proof"]>;
  messageId: string;
}) {
  const focusCitation = (citationId: string) => {
    const target = document.getElementById(citationDomId(messageId, citationId));
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      target.focus();
    }
  };
  return (
    <span className="brain-inline-citations" aria-label={t(language, "brain.answerProof.citationsLabel", { count: proof.citations.length })}>
      {proof.citations.map((citation, index) => (
        <button
          key={citation.id}
          type="button"
          className="brain-inline-citation"
          aria-label={t(language, "brain.answerProof.marker", { index: index + 1 })}
          aria-controls={citationDomId(messageId, citation.id)}
          title={citation.title}
          onClick={() => focusCitation(citation.id)}
        >
          {index + 1}
        </button>
      ))}
    </span>
  );
}

export function AnswerProofCard({
  language,
  proof,
  messageId,
  onUseEvidence,
}: {
  language: Language;
  proof: NonNullable<Message["proof"]>;
  messageId: string;
  onUseEvidence?: (prompt: string) => void;
}) {
  const [openCitation, setOpenCitation] = React.useState<Citation | null>(null);
  return (
    <section className="brain-answer-proof" role="group" aria-label={t(language, "brain.answerProof.aria")}>
      <div className="brain-answer-proof-head">
        <span>{t(language, "brain.answerProof.title")}</span>
        <strong>{proof.provenAcrossModels ? t(language, "brain.answerProof.modelProven", { model: proof.model }) : t(language, "brain.answerProof.modelPending", { model: proof.model })}</strong>
        {proof.citations.length ? (
          <small className="brain-answer-proof-count">{t(language, "brain.answerProof.citationsLabel", { count: proof.citations.length })}</small>
        ) : null}
      </div>
      {proof.citations.length ? (
        <ol>
          {proof.citations.map((citation, index) => (
            <li
              key={citation.id}
              id={citationDomId(messageId, citation.id)}
              tabIndex={-1}
              aria-label={t(language, "brain.answerProof.citationItem", { index: index + 1, title: citation.title })}
            >
              <button
                type="button"
                className="brain-citation-open"
                aria-haspopup="dialog"
                aria-label={t(language, "brain.sourceModal.openAria", { title: citation.title })}
                data-testid="citation-open"
                onClick={() => setOpenCitation(citation)}
              >
                <span className="brain-answer-proof-index" aria-hidden="true">{index + 1}</span>
                <span>{citation.source}</span>
                <strong>{citation.title}</strong>
                <CitationVisual language={language} citation={citation} />
                {/* Where inside the document — only when the chunk proves it. */}
                {citation.locator ? (
                  <em className="brain-citation-locator" data-testid="citation-locator">
                    {citation.locator}
                  </em>
                ) : null}
                <small>{citation.snippet || proof.query}</small>
                <CitationWhy language={language} citation={citation} />
                <span className="brain-citation-open-hint" aria-hidden="true">
                  {t(language, "brain.sourceModal.open")}
                </span>
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <small>{t(language, "brain.answerProof.empty")}</small>
      )}
      {onUseEvidence && proof.citations.length ? (
        <EvidenceActionRow
          language={language}
          query={proof.query}
          sourceIds={proof.citations.map((citation) => citation.id)}
          onUseEvidence={onUseEvidence}
        />
      ) : null}
      {openCitation ? (
        <SourceChunkModal
          language={language}
          citation={openCitation}
          onClose={() => setOpenCitation(null)}
        />
      ) : null}
    </section>
  );
}

// Multi-modal evidence (v11.1.0): when the citation is a picture, show the
// picture. The thumbnail is the inline data: URI stored on the Image node at
// ingest — never a path served back out of the user's disk, which would mean
// either a new static route or going around the local-file approval gate.
//
// The caption is shown only when a vision-language model actually wrote one.
// With no model there is no caption line, because "photo.png (PNG 3024x4032)"
// dressed up as a description is exactly the thing this release removed.
export function CitationVisual({
  language,
  citation,
}: {
  language: Language;
  citation: Citation;
}) {
  const isImage = citation.kind === "Image" || citation.kind === "ImageText";
  if (!isImage) return null;
  const hasThumbnail = Boolean(citation.thumbnail);
  return (
    <span className="brain-citation-visual" data-testid="citation-visual">
      {hasThumbnail ? (
        <img
          className="brain-citation-thumb"
          src={citation.thumbnail}
          alt={citation.caption || t(language, "brain.answerProof.imageAlt", { title: citation.title })}
          width={48}
          height={48}
          loading="lazy"
        />
      ) : (
        <span className="brain-citation-thumb is-missing" aria-hidden="true">
          {t(language, "brain.answerProof.imageBadge")}
        </span>
      )}
      <span className="brain-citation-visual-text">
        {citation.caption ? (
          <em data-testid="citation-caption">{citation.caption}</em>
        ) : (
          <em className="is-muted" data-testid="citation-caption-absent">
            {t(language, "brain.answerProof.noCaption")}
          </em>
        )}
      </span>
    </span>
  );
}

// Evidence → action (v9.9.6): the sources this answer actually used become
// one-click follow-ups. The server composes evidence-scoped prompts; clicking
// one sends it through the normal chat path, so there is no second, weaker
// generation road. Actions are fetched lazily — an answer nobody follows up on
// costs nothing.
export function EvidenceActionRow({
  language,
  query,
  sourceIds,
  onUseEvidence,
}: {
  language: Language;
  query: string;
  sourceIds: string[];
  onUseEvidence: (prompt: string) => void;
}) {
  const [state, setState] = React.useState<
    | { status: "idle" }
    | { status: "loading" }
    | { status: "ready"; actions: EvidenceAction[]; reason: string }
  >({ status: "idle" });

  const load = React.useCallback(() => {
    setState({ status: "loading" });
    void latticeApi.evidenceActions(query, sourceIds, language).then((result) => {
      const payload = result.data;
      setState({
        status: "ready",
        actions: result.ok ? payload.actions : [],
        reason: result.ok ? payload.reason : result.error || "",
      });
    });
  }, [language, query, sourceIds]);

  if (state.status === "idle") {
    return (
      <div className="brain-evidence-actions">
        <button
          type="button"
          className="brain-evidence-action-trigger"
          data-testid="evidence-actions-open"
          onClick={load}
        >
          {t(language, "brain.evidenceActions.open")}
        </button>
      </div>
    );
  }
  if (state.status === "loading") {
    return (
      <div className="brain-evidence-actions">
        <small role="status">{t(language, "brain.evidenceActions.loading")}</small>
      </div>
    );
  }
  if (!state.actions.length) {
    return (
      <div className="brain-evidence-actions">
        <small className="is-error" role="status">
          {state.reason || t(language, "brain.evidenceActions.unavailable")}
        </small>
      </div>
    );
  }
  return (
    <div className="brain-evidence-actions" data-testid="evidence-actions">
      <small>{t(language, "brain.evidenceActions.title")}</small>
      <div className="brain-evidence-action-list">
        {state.actions.map((action) => (
          <button
            key={action.id}
            type="button"
            className="brain-evidence-action"
            data-testid={`evidence-action-${action.id}`}
            title={action.suggested_path || undefined}
            onClick={() => onUseEvidence(action.prompt)}
          >
            {action.label[language === "ko" ? "ko" : "en"]}
          </button>
        ))}
      </div>
    </div>
  );
}

// Evidence explainability: show *why* the Brain picked a citation — which
// query words matched and how confident the recall is — instead of asking
// the user to trust a bare list.
function CitationWhy({
  language,
  citation,
}: {
  language: Language;
  citation: Citation;
}) {
  const hasTerms = citation.matchedTerms.length > 0;
  return (
    <span className="brain-citation-why" aria-label={t(language, "brain.answerProof.why.aria")}>
      <em className={`brain-citation-confidence is-${citation.confidence}`}>
        {t(language, `brain.answerProof.confidence.${citation.confidence}`)}
      </em>
      {hasTerms ? (
        <>
          <span className="brain-citation-why-label">{t(language, "brain.answerProof.matched")}</span>
          {citation.matchedTerms.slice(0, 4).map((term) => (
            <mark key={term}>{term}</mark>
          ))}
        </>
      ) : (
        <span className="brain-citation-why-label">{t(language, "brain.answerProof.matched.none")}</span>
      )}
    </span>
  );
}

type SourceNode = {
  title: string;
  type: string;
  summary: string;
  provenance: Array<{ key: string; value: string }>;
};

// Metadata keys worth surfacing as provenance ("where this text came from").
const PROVENANCE_KEYS = [
  "source",
  "source_uri",
  "origin",
  "relative_path",
  "path",
  "filename",
  "conversation_id",
  "provenance_id",
];

// GET /api/graph/node → {node: {id,type,title,summary,metadata,...}}.
// Defensive parse; null when the payload has no node record.
export function parseSourceNode(data: unknown): SourceNode | null {
  const root = isRecord(data) ? data : {};
  const node = isRecord(root.node) ? root.node : null;
  if (!node) return null;
  const metadata = isRecord(node.metadata) ? node.metadata : {};
  const provenance = PROVENANCE_KEYS.flatMap((key) => {
    const value = textValue(metadata, [key]);
    return value ? [{ key, value }] : [];
  });
  return {
    title: textValue(node, ["title", "label", "id"]),
    type: textValue(node, ["type"]),
    summary: textValue(node, ["summary", "content", "text"]),
    provenance,
  };
}

// The cited source's stored text (graph node summary) + provenance, in the
// same modal shell as file previews. The honest failure mode matters:
// citations that are not graph nodes (older recalls) show a clear error
// instead of pretending there is nothing to see.
function SourceChunkModal({
  language,
  citation,
  onClose,
}: {
  language: Language;
  citation: Citation;
  onClose: () => void;
}) {
  const trapRef = useFocusTrap<HTMLDivElement>(onClose);
  const [state, setState] = React.useState<
    { status: "loading" } | { status: "error"; reason: string } | { status: "ready"; node: SourceNode }
  >({ status: "loading" });

  React.useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    void latticeApi.graphNode(citation.id).then((result) => {
      if (cancelled) return;
      const node = result.ok ? parseSourceNode(result.data) : null;
      if (node) setState({ status: "ready", node });
      else setState({ status: "error", reason: result.error || String(result.status || "") });
    });
    return () => {
      cancelled = true;
    };
  }, [citation.id]);

  const title = state.status === "ready" && state.node.title ? state.node.title : citation.title;
  return (
    <div
      className="file-preview-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={trapRef}
        className="file-preview-modal source-chunk-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t(language, "brain.sourceModal.aria", { title })}
        data-testid="source-chunk-modal"
      >
        <header className="file-preview-head">
          <strong className="file-preview-name">{title}</strong>
          <div className="file-preview-actions">
            {state.status === "ready" && state.node.type ? (
              <span className="source-chunk-type">{state.node.type}</span>
            ) : null}
            <button
              type="button"
              className="file-preview-close"
              aria-label={t(language, "brain.sourceModal.close")}
              onClick={onClose}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </header>
        <div className="file-preview-body source-chunk-body">
          {state.status === "loading" ? (
            <p className="file-preview-note" role="status">{t(language, "brain.sourceModal.loading")}</p>
          ) : state.status === "error" ? (
            <p className="file-preview-note is-error" role="alert">
              {t(language, "brain.sourceModal.error", { reason: state.reason })}
            </p>
          ) : (
            <>
              {state.node.summary ? (
                <pre className="source-chunk-text" data-testid="source-chunk-text">{state.node.summary}</pre>
              ) : (
                <p className="file-preview-note">{t(language, "brain.sourceModal.empty")}</p>
              )}
              {state.node.provenance.length ? (
                <div className="source-chunk-provenance" data-testid="source-chunk-provenance">
                  <small>{t(language, "brain.sourceModal.provenance")}</small>
                  <ul>
                    {state.node.provenance.map((entry) => (
                      <li key={entry.key}>
                        <span>{entry.key}</span>
                        <code>{entry.value}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
