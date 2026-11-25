import * as React from "react";
import { BookOpen, Link2, MessageSquare, Sparkles } from "lucide-react";

import type { ChronicleDay } from "@/api/client";
import { navigateHash } from "@/features/brain/navigation";
import { t, type Language } from "@/i18n";
import { fmtNumber } from "@/lib/utils";

const VISIBLE = 8;

const OPEN_MEMORY = "/hybrid-search";
const OPEN_MAP = "/knowledge-graph";
const OPEN_CONVERSATIONS = "/brain";

type StoryItem = {
  key: string;
  title: string;
  meta: string;
  target: string;
  openLabel: string;
};

export function DayStory({
  day,
  loading,
  language,
}: {
  day: ChronicleDay | null;
  loading: boolean;
  language: Language;
}) {
  const heading = (
    <header className="chronicle-panel-head">
      <h2 className="chronicle-panel-title">{t(language, "chronicle.day.title")}</h2>
      <p className="chronicle-panel-hint" data-testid="chronicle-day-date">{day ? day.date : ""}</p>
    </header>
  );

  if (loading || !day) {
    return (
      <section className="chronicle-panel chronicle-day" data-testid="chronicle-day">
        {heading}
        <p className="chronicle-note" role="status">{t(language, "chronicle.day.loading")}</p>
      </section>
    );
  }

  const total = day.counts.sources + day.counts.entities + day.counts.conversations + day.counts.changes;

  return (
    <section className="chronicle-panel chronicle-day" data-testid="chronicle-day">
      {heading}
      {total === 0 ? (
        <p className="chronicle-note" data-testid="chronicle-day-quiet">{t(language, "chronicle.day.quiet")}</p>
      ) : (
        <div className="chronicle-day-groups">
          <StoryGroup
            titleKey="chronicle.group.sources"
            icon={<BookOpen aria-hidden="true" />}
            count={day.counts.sources}
            items={sourceItems(day, language)}
            language={language}
          />
          <StoryGroup
            titleKey="chronicle.group.entities"
            icon={<Sparkles aria-hidden="true" />}
            count={day.counts.entities}
            items={entityItems(day, language)}
            language={language}
          />
          <StoryGroup
            titleKey="chronicle.group.conversations"
            icon={<MessageSquare aria-hidden="true" />}
            count={day.counts.conversations}
            items={conversationItems(day, language)}
            language={language}
          />
          <StoryGroup
            titleKey="chronicle.group.changes"
            icon={<Link2 aria-hidden="true" />}
            count={day.counts.changes}
            items={changeItems(day, language)}
            language={language}
          />
        </div>
      )}
    </section>
  );
}

function StoryGroup({
  titleKey,
  icon,
  count,
  items,
  language,
}: {
  titleKey: string;
  icon: React.ReactNode;
  count: number;
  items: StoryItem[];
  language: Language;
}) {
  const shown = items.slice(0, VISIBLE);
  const remaining = Math.max(count - shown.length, 0);
  return (
    <article className="chronicle-group" data-testid="chronicle-group">
      <h3 className="chronicle-group-title">
        <span className="chronicle-group-icon">{icon}</span>
        {t(language, titleKey)}
        <span className="chronicle-group-count">{t(language, "chronicle.group.count", { count: fmtNumber(count) })}</span>
      </h3>
      {shown.length === 0 ? (
        <p className="chronicle-group-empty">{t(language, "chronicle.group.empty")}</p>
      ) : (
        <ul className="chronicle-group-list">
          {shown.map((item) => (
            <li key={item.key}>
              <button
                type="button"
                className="chronicle-item"
                aria-label={`${item.title} — ${item.openLabel}`}
                onClick={() => navigateHash(item.target)}
              >
                <span className="chronicle-item-title">{item.title}</span>
                <span className="chronicle-item-meta">{item.meta}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {remaining > 0 ? (
        <p className="chronicle-group-more">{t(language, "chronicle.group.more", { count: fmtNumber(remaining) })}</p>
      ) : null}
    </article>
  );
}

function sourceItems(day: ChronicleDay, language: Language): StoryItem[] {
  const openLabel = t(language, "chronicle.open.source");
  return day.groups.sources.map((source) => ({
    key: source.id,
    title: source.title,
    meta: sourceTypeLabel(source.source_type, language),
    target: OPEN_MEMORY,
    openLabel,
  }));
}

function entityItems(day: ChronicleDay, language: Language): StoryItem[] {
  const openLabel = t(language, "chronicle.open.entity");
  return day.groups.entities.map((entity) => ({
    key: entity.id,
    title: entity.label,
    meta: entityTypeLabel(entity.type, language),
    target: OPEN_MAP,
    openLabel,
  }));
}

function conversationItems(day: ChronicleDay, language: Language): StoryItem[] {
  const openLabel = t(language, "chronicle.open.conversation");
  return day.groups.conversations.map((conversation, position) => ({
    key: conversation.conversation_id || `loose-${position}`,
    title: conversation.preview || t(language, "chronicle.conversation.untitled"),
    meta: t(language, "chronicle.conversation.messages", { count: fmtNumber(conversation.messages) }),
    target: OPEN_CONVERSATIONS,
    openLabel,
  }));
}

function changeItems(day: ChronicleDay, language: Language): StoryItem[] {
  const openLabel = t(language, "chronicle.open.change");
  return day.groups.changes.map((change, position) => ({
    key: `${change.node_id}-${position}`,
    title: change.label,
    meta: changeKindLabel(change.kind, language),
    target: OPEN_MAP,
    openLabel,
  }));
}

const SOURCE_TYPE_KEYS: Record<string, string> = {
  upload: "chronicle.sourceType.upload",
  note: "chronicle.sourceType.note",
  web_url: "chronicle.sourceType.web_url",
  conversation: "chronicle.sourceType.conversation",
  local_file: "chronicle.sourceType.local_file",
  image: "chronicle.sourceType.image",
};

export function sourceTypeLabel(sourceType: string, language: Language): string {
  return t(language, SOURCE_TYPE_KEYS[sourceType] || "chronicle.sourceType.other");
}

export function entityTypeLabel(type: string, language: Language): string {
  const key = `ui.entity.${type}`;
  const label = t(language, key);
  return label === key ? t(language, "chronicle.entityType.other") : label;
}

const CHANGE_KIND_KEYS: Record<string, string> = {
  fact_superseded: "chronicle.change.fact_superseded",
  fact_retired: "chronicle.change.fact_retired",
  connection_superseded: "chronicle.change.connection_superseded",
  connection_ended: "chronicle.change.connection_ended",
};

export function changeKindLabel(kind: string, language: Language): string {
  return t(language, CHANGE_KIND_KEYS[kind] || "chronicle.change.other");
}
