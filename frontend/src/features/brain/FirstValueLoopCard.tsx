import * as React from "react";
import { FileText, MessageCircleQuestion, Rocket, Trash2, X } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { type DemoCorpusDocument, type DemoCorpusQuestion, latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import {
  dismissFirstValueLoop,
  markFirstValueLoopAsked,
  markFirstValueLoopFileGenTried,
  readFirstValueLoopState,
  shouldShowFirstValueLoop,
} from "./firstValueLoop";

// The "30초 체험" First Value Loop track (backlog #3) on the empty Brain home:
// one click installs the built-in 3-document demo corpus through the real
// ingestion pipeline, then the suggested questions become chips that go
// through the normal chat ask path (sources + grounding render as usual), and
// after the first successful ask the track suggests turning the answer into a
// real HTML file. Progress persists in localStorage; the demo data is
// removable at any time via DELETE.
export function FirstValueLoopCard({
  language,
  streaming,
  onSendText,
}: {
  language: Language;
  streaming: boolean;
  onSendText: (text: string) => void;
}) {
  const qc = useQueryClient();
  const [track, setTrack] = React.useState(readFirstValueLoopState);
  const [justInstalled, setJustInstalled] = React.useState(false);
  const statusQ = useQuery({ queryKey: ["demoCorpus"], queryFn: latticeApi.demoCorpusStatus });

  const installMutation = useMutation({
    mutationFn: latticeApi.installDemoCorpus,
    onSuccess: (result) => {
      if (result.ok) setJustInstalled(true);
      void qc.invalidateQueries({ queryKey: ["demoCorpus"] });
      for (const key of ["memoryManager", "graphPreview", "graph", "memoryBrainBrief"]) {
        void qc.invalidateQueries({ queryKey: [key] });
      }
    },
  });
  const removeMutation = useMutation({
    mutationFn: latticeApi.removeDemoCorpus,
    onSuccess: (result) => {
      void qc.invalidateQueries({ queryKey: ["demoCorpus"] });
      for (const key of ["memoryManager", "graphPreview", "graph", "memoryBrainBrief"]) {
        void qc.invalidateQueries({ queryKey: [key] });
      }
      // Removing the demo data ends the guided track — it never re-prompts.
      if (result.ok) setTrack(dismissFirstValueLoop());
    },
  });

  if (!shouldShowFirstValueLoop(track)) return null;
  // Stay silent until the server answers; a failed status (e.g. Knowledge
  // Graph disabled → 503) hides the track instead of promising a broken demo.
  if (!statusQ.data) return null;
  if (!statusQ.data.ok && !installMutation.data?.ok) return null;

  const status = statusQ.data.data;
  const installResult = installMutation.data?.ok ? installMutation.data.data : null;
  const installed = Boolean(status.installed) || Boolean(installResult);
  const documents: DemoCorpusDocument[] = installResult?.documents?.length
    ? installResult.documents
    : status.documents || [];
  const questions: DemoCorpusQuestion[] = (
    installResult?.suggested_questions?.length
      ? installResult.suggested_questions
      : status.suggested_questions || []
  ).filter((item) => typeof item.question === "string" && item.question.trim());

  const installError = installMutation.data && !installMutation.data.ok
    ? installMutation.data.error || ""
    : "";
  const removeError = removeMutation.data && !removeMutation.data.ok
    ? removeMutation.data.error || ""
    : "";

  function askDemoQuestion(question: string) {
    if (streaming) return;
    setTrack(markFirstValueLoopAsked());
    onSendText(question);
  }

  function tryFileGeneration() {
    if (streaming) return;
    setTrack(markFirstValueLoopFileGenTried());
    onSendText(t(language, "brain.fvl.filegen.prompt"));
  }

  return (
    <section className="brain-fvl" aria-label={t(language, "brain.fvl.aria")} data-testid="first-value-loop">
      <header className="brain-fvl-head">
        <span className="brain-fvl-title">
          <Rocket className="h-3.5 w-3.5" aria-hidden="true" />
          {t(language, "brain.fvl.title")}
        </span>
        <button
          type="button"
          className="brain-fvl-dismiss"
          aria-label={t(language, "brain.fvl.dismiss")}
          onClick={() => setTrack(dismissFirstValueLoop())}
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </header>

      {!installed ? (
        <>
          <p className="brain-fvl-subtitle">{t(language, "brain.fvl.subtitle")}</p>
          <button
            type="button"
            className="brain-fvl-start"
            data-testid="fvl-start"
            disabled={installMutation.isPending}
            onClick={() => installMutation.mutate()}
          >
            {installMutation.isPending
              ? t(language, "brain.fvl.starting")
              : t(language, "brain.fvl.start")}
          </button>
          {installError ? (
            <small className="brain-fvl-error" role="alert">
              {t(language, "brain.fvl.error", { reason: installError })}
            </small>
          ) : null}
        </>
      ) : (
        <>
          <p className="brain-fvl-installed" role={justInstalled ? "status" : undefined}>
            {t(language, "brain.fvl.installed", { count: documents.length })}
            {justInstalled ? (
              <span className="lattice-inflow" aria-hidden="true"><i /><i /><i /></span>
            ) : null}
          </p>
          {documents.length ? (
            <ul
              className={`brain-fvl-docs ${justInstalled ? "lattice-success-pulse" : ""}`}
              aria-label={t(language, "brain.fvl.docs.aria")}
            >
              {documents.map((doc) => (
                <li key={doc.demo_id || doc.source_uri || doc.title} data-testid="fvl-doc">
                  <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{doc.title}</span>
                  {doc.duplicate ? <small>{t(language, "brain.fvl.doc.duplicate")}</small> : null}
                </li>
              ))}
            </ul>
          ) : null}

          {questions.length ? (
            <div className="brain-fvl-questions" aria-label={t(language, "brain.fvl.chips.aria")}>
              <span className="brain-fvl-step-label">
                <MessageCircleQuestion className="h-3.5 w-3.5" aria-hidden="true" />
                {t(language, "brain.fvl.ask.title")}
              </span>
              {questions.map((question) => (
                <button
                  key={question.question}
                  type="button"
                  className="brain-fvl-chip"
                  data-testid="fvl-chip"
                  disabled={streaming}
                  title={question.expected_title || undefined}
                  onClick={() => askDemoQuestion(question.question || "")}
                >
                  {question.question}
                </button>
              ))}
            </div>
          ) : null}

          {track.asked && !track.fileGenTried ? (
            <div className="brain-fvl-next">
              <span className="brain-fvl-step-label">{t(language, "brain.fvl.next.title")}</span>
              <button
                type="button"
                className="brain-fvl-chip is-filegen"
                data-testid="fvl-filegen-chip"
                disabled={streaming}
                onClick={tryFileGeneration}
              >
                {t(language, "brain.fvl.filegen.chip")}
              </button>
            </div>
          ) : null}
          {track.fileGenTried ? (
            <p className="brain-fvl-done">{t(language, "brain.fvl.done")}</p>
          ) : null}

          <button
            type="button"
            className="brain-fvl-remove"
            data-testid="fvl-remove"
            disabled={removeMutation.isPending}
            onClick={() => removeMutation.mutate()}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            {removeMutation.isPending
              ? t(language, "brain.fvl.removing")
              : t(language, "brain.fvl.remove")}
          </button>
          {removeError ? (
            <small className="brain-fvl-error" role="alert">
              {t(language, "brain.fvl.error", { reason: removeError })}
            </small>
          ) : null}
        </>
      )}
    </section>
  );
}
