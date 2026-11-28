import * as React from "react";
import { Tabs } from "@/components/primitives";
import { t, type Language } from "@/i18n";
import { IntegrityRepairPanel } from "./IntegrityRepairPanel";
import { EntityMergePanel } from "./EntityMergePanel";
import { KnowledgeCorrectionsPanel } from "./KnowledgeCorrectionsPanel";
import { EmbeddingRegistryPanel } from "./EmbeddingRegistryPanel";
import { ClustersGapsPanel } from "./ClustersGapsPanel";
import { BrainNetworkPanel } from "../BrainNetworkPanel";

type GovernanceTab =
  | "integrity"
  | "entities"
  | "corrections"
  | "embeddings"
  | "clusters"
  | "network";

export function BrainGovernanceHub({ language }: { language: Language }) {
  const [tab, setTab] = React.useState<GovernanceTab>("integrity");

  const tabs = [
    { id: "integrity", label: t(language, "brain.gov.tab.integrity") },
    { id: "entities", label: t(language, "brain.gov.tab.entities") },
    { id: "corrections", label: t(language, "brain.gov.tab.corrections") },
    { id: "embeddings", label: t(language, "brain.gov.tab.embeddings") },
    { id: "clusters", label: t(language, "brain.gov.tab.clusters") },
    { id: "network", label: t(language, "brain.gov.tab.network") },
  ];

  return (
    <section className="space-y-3" data-testid="brain-governance-hub">
      <div>
        <h3 className="text-base font-semibold">{t(language, "brain.gov.title")}</h3>
        <p className="text-xs text-muted-foreground">{t(language, "brain.gov.subtitle")}</p>
      </div>
      <Tabs tabs={tabs} value={tab} onChange={(id) => setTab(id as GovernanceTab)} />
      {tab === "integrity" ? <IntegrityRepairPanel language={language} /> : null}
      {tab === "entities" ? <EntityMergePanel language={language} /> : null}
      {tab === "corrections" ? <KnowledgeCorrectionsPanel language={language} /> : null}
      {tab === "embeddings" ? <EmbeddingRegistryPanel language={language} /> : null}
      {tab === "clusters" ? <ClustersGapsPanel language={language} /> : null}
      {tab === "network" ? <BrainNetworkPanel language={language} /> : null}
    </section>
  );
}
