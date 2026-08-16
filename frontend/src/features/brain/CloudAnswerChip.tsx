import { t, type Language } from "@/i18n";
import type { MessageCloudAnswer } from "./types";

export function CloudAnswerChip({
  language,
  cloudAnswer,
}: {
  language: Language;
  cloudAnswer: MessageCloudAnswer;
}) {
  const label = cloudAnswer.model
    ? t(language, "brain.cloud.chip.model", { model: cloudAnswer.model })
    : t(language, "brain.cloud.chip");
  const showProposals = Boolean(
    cloudAnswer.expansion?.stagedForReview && cloudAnswer.expansion.candidateCount > 0,
  );
  const detail = showProposals
    ? t(language, "brain.cloud.detail", {
      nodes: cloudAnswer.sentNodeCount,
      proposals: cloudAnswer.expansion!.candidateCount,
    })
    : t(language, "brain.cloud.detail.nodes", { nodes: cloudAnswer.sentNodeCount });

  return (
    <details className="brain-cloud-answer" data-testid="cloud-answer-chip">
      <summary
        className="brain-cloud-answer-chip"
        aria-label={t(language, "brain.cloud.chip.expandAria")}
      >
        <span aria-hidden="true">{label}</span>
        <span className="sr-only">{t(language, "brain.cloud.chip.aria")}</span>
      </summary>
      <p className="brain-cloud-answer-detail">{detail}</p>
    </details>
  );
}
