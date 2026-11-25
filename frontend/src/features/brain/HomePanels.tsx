import * as React from "react";
import {
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Cpu,
  FileUp,
  HardDrive,
  History,
  Loader2,
  Repeat2,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";

import { t, type Language } from "@/i18n";
import { focusComposer, navigateHash } from "./navigation";
import type {
  BrainBrief,
  BrainBriefAction,
  BrainProof,
  ConversationSummary,
} from "./types";

export function ModelMissingNotice({ language }: { language: Language }) {
  const detail = t(language, "brain.noModel.banner");
  return (
    <div className="brain-model-missing" role="note" aria-label={detail} title={detail}>
      <Cpu className="h-3.5 w-3.5" aria-hidden="true" />
      <span className="brain-model-missing-text">{t(language, "brain.noModel.pill")}</span>
      <button type="button" onClick={() => navigateHash("/models")}>
        {t(language, "brain.noModel.cta")}
        <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    </div>
  );
}

export function formatConversationTime(language: Language, updatedAt?: number): string {
  if (!updatedAt) return "";
  const locale = language === "ko" ? "ko-KR" : "en-US";
  const date = new Date(updatedAt);
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  return date.toLocaleDateString(locale, { month: "short", day: "numeric" });
}

const HISTORY_COLLAPSED_COUNT = 5;
const HISTORY_EXPANDED_COUNT = 20;

export function PastConversationsPanel({
  language,
  items,
  busyId,
  onResume,
  onDelete,
}: {
  language: Language;
  items: ConversationSummary[];
  busyId: string | null;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [confirmingId, setConfirmingId] = React.useState<string | null>(null);
  const [expanded, setExpanded] = React.useState(false);
  const visible = items.slice(0, expanded ? HISTORY_EXPANDED_COUNT : HISTORY_COLLAPSED_COUNT);
  const hiddenCount = Math.min(items.length, HISTORY_EXPANDED_COUNT) - HISTORY_COLLAPSED_COUNT;
  if (!visible.length) return null;

  return (
    <section className="brain-history-panel" aria-label={t(language, "brain.history.aria")}>
      <div className="brain-history-head">
        <History className="h-4 w-4" aria-hidden="true" />
        <strong>{t(language, "brain.history.title")}</strong>
        <span>{t(language, "brain.history.hint")}</span>
      </div>
      <ul className="brain-history-list">
        {visible.map((item) => {
          const busy = busyId === item.id;
          const confirming = confirmingId === item.id;
          return (
            <li key={item.id} className={`brain-history-item${busy ? " is-busy" : ""}`}>
              <button
                type="button"
                className="brain-history-resume"
                disabled={busy}
                aria-label={t(language, "brain.history.resumeAria", { title: item.title })}
                onClick={() => onResume(item.id)}
              >
                {busy ? <Loader2 className="h-3.5 w-3.5 brain-ingest-spin" aria-hidden="true" /> : null}
                <span className="brain-history-title">{item.title}</span>
                <small>
                  {t(language, "brain.history.messages", { count: item.messageCount })}
                  {item.updatedAt ? ` · ${formatConversationTime(language, item.updatedAt)}` : ""}
                </small>
              </button>
              <button
                type="button"
                className={`brain-history-delete${confirming ? " is-confirming" : ""}`}
                disabled={busy}
                aria-label={
                  confirming
                    ? t(language, "brain.history.deleteConfirm")
                    : t(language, "brain.history.deleteAria", { title: item.title })
                }
                onClick={() => {
                  if (confirming) {
                    setConfirmingId(null);
                    onDelete(item.id);
                  } else {
                    setConfirmingId(item.id);
                  }
                }}
                onBlur={() => setConfirmingId((current) => (current === item.id ? null : current))}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                {confirming ? <span>{t(language, "brain.history.deleteConfirm")}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
      {hiddenCount > 0 ? (
        <button
          type="button"
          className="brain-history-more"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? (
            <>
              <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
              {t(language, "brain.history.showLess")}
            </>
          ) : (
            <>
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              {t(language, "brain.history.showMore", { count: hiddenCount })}
            </>
          )}
        </button>
      ) : null}
    </section>
  );
}

export function handleBriefAction(action: BrainBriefAction, onVerifyModelContinuity: () => void) {
  if (action.id === "ask_brain") {
    focusComposer();
    return;
  }
  if (action.id === "verify_model") {
    onVerifyModelContinuity();
    return;
  }
  if (action.route) {
    navigateHash(action.route);
  }
}

export function BrainBriefPanel({
  language,
  brief,
  showEvidence = true,
  onAction,
}: {
  language: Language;
  brief: BrainBrief;
  showEvidence?: boolean;
  onAction: (action: BrainBriefAction) => void;
}) {
  const focusTitle = brief.focus.title || t(language, "brain.brief.focus.empty");
  const focusDetail = brief.focus.detail || t(language, "brain.brief.focus.empty.detail");
  const actions = brief.nextActions.slice(0, 3);

  return (
    <section className="brain-brief-panel" aria-label={t(language, "brain.brief.aria")}>
      <div className="brain-brief-copy">
        <span>{t(language, "brain.brief.kicker")}</span>
        <strong>{t(language, brief.headlineKey)}</strong>
        <p>{t(language, brief.bodyKey, { focus: focusTitle })}</p>
      </div>
      <div className="brain-brief-focus">
        <Sparkles className="h-4 w-4" />
        <div>
          <span>{t(language, "brain.brief.focus")}</span>
          <strong>{focusTitle}</strong>
          <small>{focusDetail}</small>
        </div>
      </div>
      {showEvidence ? (
        <div className="brain-brief-evidence" aria-label={t(language, "brain.brief.evidence.aria")}>
          {brief.evidence.slice(0, 3).map((item) => (
            <span key={item.id} title={t(language, item.detailKey)}>
              <strong>{item.value}</strong>
              {t(language, item.labelKey)}
            </span>
          ))}
        </div>
      ) : null}
      {actions.length ? (
        <div className="brain-brief-actions" aria-label={t(language, "brain.brief.actions")}>
          {actions.map((action) => (
            <button key={action.id} type="button" onClick={() => onAction(action)}>
              {briefActionIcon(action.id)}
              <span>{t(language, action.labelKey)}</span>
              <small>{t(language, action.detailKey)}</small>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function briefActionIcon(id: string) {
  if (id === "add_source") return <FileUp className="h-4 w-4" />;
  if (id === "inspect_topics") return <Search className="h-4 w-4" />;
  if (id === "verify_model") return <Repeat2 className="h-4 w-4" />;
  if (id === "backup_brain") return <HardDrive className="h-4 w-4" />;
  return <ArrowRight className="h-4 w-4" />;
}

export function ModelContinuityDemo({
  language,
  proof,
  modelName,
  onVerify,
}: {
  language: Language;
  proof: BrainProof;
  modelName: string;
  onVerify: () => void;
}) {
  return (
    <section className="brain-model-demo" aria-label={t(language, "brain.modelDemo.aria")}>
      <div>
        <span>{t(language, "brain.modelDemo.kicker")}</span>
        <strong>{proof.modelContinuity.proven ? t(language, "brain.modelDemo.proven") : t(language, "brain.modelDemo.pending")}</strong>
        <small>{t(language, "brain.modelDemo.detail", { model: proof.modelContinuity.activeModel || modelName })}</small>
      </div>
      <button type="button" onClick={onVerify}>
        <Repeat2 className="h-3.5 w-3.5" />
        {t(language, "brain.modelDemo.verify")}
      </button>
      <button type="button" onClick={() => navigateHash("/models")}>
        <Cpu className="h-3.5 w-3.5" />
        {t(language, "brain.modelDemo.change")}
      </button>
    </section>
  );
}
