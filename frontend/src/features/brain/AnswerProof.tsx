import { t, type Language } from "@/i18n";
import type { Message } from "./types";

function citationDomId(messageId: string, citationId: string): string {
  return `${messageId}-cite-${citationId}`;
}

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

export function AnswerProofCard({ language, proof, messageId }: { language: Language; proof: NonNullable<Message["proof"]>; messageId: string }) {
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
              <span className="brain-answer-proof-index" aria-hidden="true">{index + 1}</span>
              <span>{citation.source}</span>
              <strong>{citation.title}</strong>
              <small>{citation.snippet || proof.query}</small>
              <CitationWhy language={language} citation={citation} />
            </li>
          ))}
        </ol>
      ) : (
        <small>{t(language, "brain.answerProof.empty")}</small>
      )}
    </section>
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
  citation: NonNullable<Message["proof"]>["citations"][number];
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
