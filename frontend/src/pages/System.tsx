import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
import "@/i18n/workspace";
import { ShieldCheck } from "lucide-react";
import { Tabs } from "@/components/primitives";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { navigateHash } from "@/features/brain/navigation";
import { AccountPanel, WorkspacePanel } from "@/features/system/SystemAccountPanels";
import { ActivityPanel, AdminPanel, NetworkPanel, SnapshotsPanel } from "@/features/system/SystemOperationsPanels";
import { SettingsPanel } from "@/features/system/SettingsPanel";

type SystemTab = "account" | "workspaces" | "snapshots" | "activity" | "network" | "settings" | "admin";

type SystemGroup = "identity" | "data" | "system";

// Seven equal-looking tabs made someone read all seven to find one. They are
// the same seven destinations, sorted into the three questions people actually
// arrive with: who am I, where is my data, how does this machine behave.
const groups: Array<{ id: SystemGroup; labelKey: string }> = [
  { id: "identity", labelKey: "system.group.identity" },
  { id: "data", labelKey: "system.group.data" },
  { id: "system", labelKey: "system.group.system" },
];

const tabs: Array<{ id: SystemTab; labelKey: string; group: SystemGroup }> = [
  { id: "account", labelKey: "system.tab.account", group: "identity" },
  { id: "workspaces", labelKey: "system.tab.workspaces", group: "identity" },
  { id: "snapshots", labelKey: "system.tab.snapshots", group: "data" },
  { id: "activity", labelKey: "system.tab.activity", group: "data" },
  { id: "settings", labelKey: "system.tab.settings", group: "system" },
  { id: "network", labelKey: "system.tab.network", group: "system" },
  { id: "admin", labelKey: "system.tab.admin", group: "system" },
];

export function SystemPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [tab, setTab] = React.useState<SystemTab>((initialTab as SystemTab) || "account");
  React.useEffect(() => {
    if (tabs.some((item) => item.id === initialTab)) setTab(initialTab as SystemTab);
  }, [initialTab]);
  const visibleTabs = mode === "basic"
    ? tabs.filter((item) => item.id === "account" || item.id === "workspaces" || item.id === "snapshots" || item.id === "settings")
    : tabs;
  const selectTab = (next: SystemTab) => {
    setTab(next);
    navigateHash("/" + ({
      account: "account",
      workspaces: "workspace-admin",
      snapshots: "snapshots",
      activity: "activity",
      network: "network",
      settings: "settings",
      admin: "system-admin",
    } as const)[next]);
  };
  return (
    <div className="product-page settings-page space-y-5 pb-8">
      <header className="page-hero">
        <div className="page-kicker"><ShieldCheck className="h-4 w-4" /> {t(language, "system.kicker")}</div>
        <h1 className="page-title">{t(language, "system.title")}</h1>
        <p className="page-copy">{t(language, "system.body")}</p>
      </header>
      <div className="system-tab-groups" data-testid="system-tab-groups">
        {groups
          .map((group) => ({ group, items: visibleTabs.filter((item) => item.group === group.id) }))
          .filter(({ items }) => items.length > 0)
          .map(({ group, items }) => {
            const labelId = `system-group-${group.id}`;
            return (
              <div key={group.id} className="system-tab-group">
                <span id={labelId} className="system-tab-group-label">
                  {t(language, group.labelKey)}
                </span>
                <Tabs
                  ariaLabelledBy={labelId}
                  tabs={items.map((item) => ({ id: item.id, label: t(language, item.labelKey) }))}
                  value={tab}
                  onChange={(id) => selectTab(id as SystemTab)}
                />
              </div>
            );
          })}
      </div>
      {tab === "account" ? <AccountPanel /> : null}
      {tab === "workspaces" ? <WorkspacePanel /> : null}
      {tab === "settings" ? <SettingsPanel /> : null}
      {tab === "snapshots" ? <SnapshotsPanel /> : null}
      {tab === "activity" ? <ActivityPanel /> : null}
      {tab === "network" ? <NetworkPanel /> : null}
      {tab === "admin" ? <AdminPanel /> : null}
    </div>
  );
}
