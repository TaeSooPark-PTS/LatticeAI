import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  Brain,
  Command,
  CornerDownLeft,
  Database,
  FileSearch,
  FolderInput,
  Library,
  MessageCircle,
  Search,
  Settings,
  Sunrise,
  Workflow,
} from "lucide-react";
import { latticeApi } from "@/api/client";
import { asArray } from "@/lib/utils";
import { t, type Language } from "@/i18n";
import { navigateHash } from "@/features/brain/navigation";

type PaletteItem = {
  id: string;
  group: "proactive" | "pages" | "knowledge" | "conversation" | "automation";
  title: string;
  detail?: string;
  icon: React.ReactNode;
  target: string;
  // Optional extra behavior on activation, e.g. asking the briefing panel to
  // expand itself after navigation. Search behavior stays untouched.
  action?: "open-briefing";
};

type SearchGroup = { kind: string; items: Record<string, unknown>[] };

const PAGE_ENTRIES: { id: string; labelKey: string; target: string; icon: React.ReactNode }[] = [
  { id: "page-brain", labelKey: "shell.route.brain", target: "/brain", icon: <Brain className="h-4 w-4" /> },
  { id: "page-capture", labelKey: "shell.route.capture", target: "/capture", icon: <FolderInput className="h-4 w-4" /> },
  { id: "page-memory", labelKey: "shell.route.memory", target: "/hybrid-search", icon: <Database className="h-4 w-4" /> },
  { id: "page-library", labelKey: "shell.route.library", target: "/models", icon: <Library className="h-4 w-4" /> },
  { id: "page-act", labelKey: "shell.route.act", target: "/agents", icon: <Workflow className="h-4 w-4" /> },
  { id: "page-review", labelKey: "command.page.review", target: "/act/review", icon: <FileSearch className="h-4 w-4" /> },
  { id: "page-system", labelKey: "shell.route.system", target: "/settings", icon: <Settings className="h-4 w-4" /> },
];

function useDebounced(value: string, delayMs: number) {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export function CommandPalette({ language, initialOpen = false }: { language: Language; initialOpen?: boolean }) {
  const [open, setOpen] = React.useState(initialOpen);
  const [query, setQuery] = React.useState("");
  const [activeIndex, setActiveIndex] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const debouncedQuery = useDebounced(query.trim(), 220);

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    const onOpenEvent = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("lattice:open-command", onOpenEvent);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("lattice:open-command", onOpenEvent);
    };
  }, []);

  React.useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  const searchQ = useQuery({
    queryKey: ["commandSearch", debouncedQuery],
    queryFn: () => latticeApi.commandSearch(debouncedQuery),
    enabled: open && debouncedQuery.length > 0,
  });

  // Cold-open proactivity: fetch the (cached) briefing once the palette opens
  // so we can suggest concrete next moves before the user types anything.
  const briefingQ = useQuery({
    queryKey: ["commandBriefing"],
    queryFn: latticeApi.commandBriefing,
    enabled: open,
    staleTime: 60_000,
  });

  const items = React.useMemo<PaletteItem[]>(() => {
    const needle = query.trim().toLowerCase();

    // Before the user types: a small proactive section built from real
    // briefing data. If the fetch failed, the section simply does not appear.
    const proactive: PaletteItem[] = [];
    if (!needle && briefingQ.data?.ok) {
      const briefing = (briefingQ.data.data || {}) as Record<string, unknown>;
      const sections = (briefing.sections || {}) as Record<string, unknown>;
      const review = (sections.review || {}) as Record<string, unknown>;
      const suggestions = (sections.suggestions || {}) as Record<string, unknown>;
      const suggestionCount = Number(suggestions.count ?? 0) || 0;
      const pendingCount = Number(review.pending ?? 0) || 0;
      proactive.push({
        id: "proactive-briefing",
        group: "proactive",
        title: t(language, "command.proactive.briefing"),
        detail: suggestionCount > 0 ? t(language, "command.proactive.suggestions", { count: suggestionCount }) : undefined,
        icon: <Sunrise className="h-4 w-4" />,
        target: "/brain",
        action: "open-briefing",
      });
      proactive.push({
        id: "proactive-review",
        group: "proactive",
        title: t(language, "command.proactive.review"),
        detail: pendingCount > 0 ? t(language, "command.proactive.pending", { count: pendingCount }) : undefined,
        icon: <FileSearch className="h-4 w-4" />,
        target: "/act/review",
      });
      proactive.push({
        id: "proactive-ask",
        group: "proactive",
        title: t(language, "briefing.action.askBrain"),
        icon: <MessageCircle className="h-4 w-4" />,
        target: "/brain",
      });
    }
    const pages: PaletteItem[] = PAGE_ENTRIES.map((entry) => ({
      id: entry.id,
      group: "pages" as const,
      title: t(language, entry.labelKey),
      icon: entry.icon,
      target: entry.target,
    })).filter((entry) => !needle || entry.title.toLowerCase().includes(needle));

    const groups = asArray<SearchGroup>((searchQ.data?.data as Record<string, unknown> | undefined)?.groups);
    const remote: PaletteItem[] = [];
    for (const group of groups) {
      for (const raw of asArray<Record<string, unknown>>(group.items)) {
        if (group.kind === "knowledge") {
          remote.push({
            id: `kg-${String(raw.id ?? remote.length)}`,
            group: "knowledge",
            title: String(raw.title || raw.id || ""),
            detail: String(raw.summary || raw.type || ""),
            icon: <Database className="h-4 w-4" />,
            target: "/brain/graph",
          });
        } else if (group.kind === "conversation") {
          remote.push({
            id: `conv-${String(raw.conversation_id ?? remote.length)}`,
            group: "conversation",
            title: String(raw.snippet || ""),
            detail: String(raw.timestamp || "").slice(0, 10),
            icon: <MessageCircle className="h-4 w-4" />,
            target: "/brain",
          });
        } else if (group.kind === "automation") {
          remote.push({
            id: `wf-${String(raw.id ?? remote.length)}`,
            group: "automation",
            title: String(raw.name || ""),
            detail: raw.enabled ? t(language, "command.automation.enabled") : t(language, "command.automation.draft"),
            icon: <Bot className="h-4 w-4" />,
            target: "/act/workflows",
          });
        }
      }
    }
    return [...proactive, ...remote, ...pages];
  }, [language, query, searchQ.data, briefingQ.data]);

  React.useEffect(() => {
    setActiveIndex(0);
  }, [items.length, debouncedQuery]);

  const close = React.useCallback(() => setOpen(false), []);

  const activate = React.useCallback(
    (item: PaletteItem | undefined) => {
      if (!item) return;
      close();
      navigateHash(item.target);
      if (item.action === "open-briefing") {
        // Best effort: an already-mounted briefing panel expands and scrolls
        // into view; otherwise the user still lands where the briefing lives.
        window.dispatchEvent(new Event("lattice:open-briefing"));
      }
    },
    [close],
  );

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, Math.max(items.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      activate(items[activeIndex]);
    }
  };

  if (!open) return null;

  const groupLabels: Record<PaletteItem["group"], string> = {
    proactive: t(language, "command.proactive.title"),
    pages: t(language, "command.group.pages"),
    knowledge: t(language, "command.group.knowledge"),
    conversation: t(language, "command.group.conversation"),
    automation: t(language, "command.group.automation"),
  };

  let lastGroup: PaletteItem["group"] | null = null;

  return (
    <div
      className="command-palette-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        className="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label={t(language, "command.title")}
        data-testid="command-palette"
      >
        <div className="command-palette-input">
          <Search className="h-4 w-4" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder={t(language, "command.placeholder")}
            aria-label={t(language, "command.title")}
            autoComplete="off"
            spellCheck={false}
          />
          <span className="command-palette-hint" aria-hidden="true">
            <Command className="h-3 w-3" />
            {t(language, "command.shortcutKey")}
          </span>
        </div>
        <div className="command-palette-results" role="listbox" aria-label={t(language, "command.results")}>
          {searchQ.isFetching ? (
            <p className="command-palette-note">{t(language, "command.searching")}</p>
          ) : null}
          {items.length === 0 && !searchQ.isFetching ? (
            <p className="command-palette-note">{t(language, "command.empty")}</p>
          ) : null}
          {items.map((item, index) => {
            const header = item.group !== lastGroup ? (
              <p key={`h-${item.group}`} className="command-palette-group">{groupLabels[item.group]}</p>
            ) : null;
            lastGroup = item.group;
            return (
              <React.Fragment key={item.id}>
                {header}
                <button
                  type="button"
                  role="option"
                  aria-selected={index === activeIndex}
                  className={`command-palette-item ${index === activeIndex ? "is-active" : ""}`}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => activate(item)}
                >
                  {item.icon}
                  <span className="command-palette-item-title">{item.title}</span>
                  {item.detail ? <span className="command-palette-item-detail">{item.detail}</span> : null}
                  {index === activeIndex ? <CornerDownLeft className="h-3.5 w-3.5" aria-hidden="true" /> : null}
                </button>
              </React.Fragment>
            );
          })}
        </div>
        <p className="command-palette-footer">{t(language, "command.footer")}</p>
      </div>
    </div>
  );
}
