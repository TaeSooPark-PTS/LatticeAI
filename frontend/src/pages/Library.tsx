import * as React from "react";
import "@/i18n/workspace";
import { Boxes } from "lucide-react";
import { Tabs } from "@/components/primitives";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { navigateHash } from "@/features/brain/navigation";
import { ActiveModelCard } from "@/features/library/ActiveModelCard";
import { ModelsPanel } from "@/features/library/ModelsPanel";
import { MarketplacePanel, McpPanel, SkillsPanel } from "@/features/library/ExtensionsPanels";
import {
  describeComputer,
  embeddingStateLabel,
  isUntranslatedProse,
} from "@/features/library/libraryDomain";

export { describeComputer, embeddingStateLabel, isUntranslatedProse };

type LibraryTab = "models" | "skills" | "mcp" | "marketplace";

const tabs: Array<{ id: LibraryTab; labelKey: string }> = [
  { id: "models", labelKey: "library.tab.models" },
  { id: "skills", labelKey: "library.tab.skills" },
  { id: "mcp", labelKey: "library.tab.mcp" },
  { id: "marketplace", labelKey: "library.tab.marketplace" },
];

export function LibraryPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [tab, setTab] = React.useState<LibraryTab>((initialTab as LibraryTab) || "models");
  React.useEffect(() => {
    if (tabs.some((item) => item.id === initialTab)) setTab(initialTab as LibraryTab);
  }, [initialTab]);
  const visibleTabs = (mode === "basic" ? tabs.filter((item) => item.id === "models") : tabs)
    .map((item) => ({ id: item.id, label: t(language, item.labelKey) }));
  const selectTab = (next: LibraryTab) => {
    setTab(next);
    navigateHash("/" + next);
  };
  return (
    <div className="product-page library-page space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Boxes className="h-4 w-4" /> {t(language, "library.kicker")}</div>
        <h1 className="page-title">{t(language, "library.title")}</h1>
        <p className="page-copy">{t(language, "library.body")}</p>
      </header>
      <ActiveModelCard />
      <Tabs tabs={visibleTabs} value={tab} onChange={(id) => selectTab(id as LibraryTab)} />
      {tab === "models" ? <ModelsPanel /> : null}
      {tab === "skills" ? <SkillsPanel /> : null}
      {tab === "mcp" ? <McpPanel /> : null}
      {tab === "marketplace" ? <MarketplacePanel /> : null}
    </div>
  );
}
