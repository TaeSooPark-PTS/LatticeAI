import * as React from "react";
import { DatabaseZap, ShieldCheck } from "lucide-react";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { t, type Language } from "@/i18n";
import type { BrainDepth, BrainProof, BrainReadiness, KnowledgeConcept, MemoryFragment, Message } from "./types";
import { BrainCarePanel } from "./BrainCarePanel";
import { BrainComposer } from "./BrainComposer";
import { BrainOverviewPanel } from "./BrainOverviewPanel";

export function BrainConversation({
  language,
  explorationDepth,
  modelName,
  messages,
  starterPrompts,
  memoryFeedback,
  draft,
  streaming,
  imageData,
  streamRef,
  memories,
  concepts,
  readiness,
  proof,
  onOpenDepth,
  onDraftChange,
  onImageDataChange,
  onSend,
}: {
  language: Language;
  explorationDepth: BrainDepth;
  modelName: string;
  messages: Message[];
  starterPrompts: string[];
  memoryFeedback: string | null;
  draft: string;
  streaming: boolean;
  imageData: string | null;
  streamRef: React.RefObject<HTMLDivElement | null>;
  memories: MemoryFragment[];
  concepts: KnowledgeConcept[];
  readiness: BrainReadiness;
  proof: BrainProof;
  onOpenDepth: (depth: BrainDepth) => void;
  onDraftChange: (value: string) => void;
  onImageDataChange: (value: string | null) => void;
  onSend: () => void;
}) {
  return (
    <section className="brain-conversation" aria-label={t(language, "brain.aria.conversation")}>
      <div className="brain-conversation-header">
        <div>
          <h1>{t(language, "brain.title")}</h1>
          <span>{t(language, `brain.depth.${explorationDepth}`)}</span>
        </div>
        <LanguageSwitcher compact />
        <div className="brain-ownership-strip" aria-label={t(language, "brain.aria.ownership")}>
          <span>{t(language, "brain.local")}</span>
          <span>{t(language, "brain.portable")}</span>
          <span>{t(language, "brain.private")}</span>
        </div>
        <div>{modelName}</div>
        <button className="brain-admin-link" type="button" onClick={() => navigateHash("/admin")}>
          <ShieldCheck className="h-3.5 w-3.5" />
          {t(language, "brain.admin")}
        </button>
      </div>

      <div ref={streamRef} className="brain-stream">
        <BrainOverviewPanel
          memories={memories}
          concepts={concepts}
          readiness={readiness}
          proof={proof}
          onOpenDepth={onOpenDepth}
        />
        {messages.length === 0 ? (
          <BrainEmptyState language={language} starterPrompts={starterPrompts} onDraftChange={onDraftChange} />
        ) : (
          messages.map((message, index) => (
            <div key={`${message.role}-${index}`} className={`brain-message ${message.role}`}>
              <div className="brain-message-bubble">{message.content}</div>
            </div>
          ))
        )}
      </div>

      {memoryFeedback ? (
        <div className="brain-save-feedback" role="status">
          <DatabaseZap className="h-3.5 w-3.5" />
          <span>{memoryFeedback}</span>
          <small>{t(language, "brain.saved.detail")}</small>
        </div>
      ) : null}

      <BrainCarePanel language={language} />

      <BrainComposer
        language={language}
        draft={draft}
        streaming={streaming}
        imageData={imageData}
        onDraftChange={onDraftChange}
        onImageDataChange={onImageDataChange}
        onSend={onSend}
      />
    </section>
  );
}

function BrainEmptyState({
  language,
  starterPrompts,
  onDraftChange,
}: {
  language: Language;
  starterPrompts: string[];
  onDraftChange: (value: string) => void;
}) {
  return (
    <div className="mind-empty">
      <div className="mind-empty-kicker">{t(language, "brain.empty.kicker")}</div>
      <div className="mind-empty-title">{t(language, "brain.empty.title")}</div>
      <p>{t(language, "brain.empty.body")}</p>
      <div className="mind-empty-prompts" aria-label={t(language, "brain.aria.starterPrompts")}>
        {starterPrompts.map((prompt) => (
          <button key={prompt} type="button" onClick={() => onDraftChange(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
      <div className="mind-empty-trail" aria-label={t(language, "brain.empty.trail.label")}>
        <span>{t(language, "brain.empty.trail.save")}</span>
        <span>{t(language, "brain.empty.trail.recall")}</span>
        <span>{t(language, "brain.empty.trail.backup")}</span>
      </div>
    </div>
  );
}

function navigateHash(route: string) {
  window.location.hash = route;
}
