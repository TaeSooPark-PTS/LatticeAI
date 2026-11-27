import * as React from "react";
import "@/i18n/workspace";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";
import { asArray, isRecord } from "@/lib/utils";

type TriggerNode = Record<string, unknown>;

const isBrainEventTrigger = (node: TriggerNode) =>
  node.type === "trigger" && isRecord(node.config) && node.config.trigger === "brain_event";

export function BrainEventTriggerForm({ workflow, language }: { workflow: Record<string, unknown>; language: Language }) {
  const nodes = asArray<TriggerNode>(workflow.nodes);
  const node = nodes.find(isBrainEventTrigger);
  if (!node) return null;
  return (
    <TriggerFields
      workflowId={String(workflow.id || workflow.workflow_id)}
      nodes={nodes}
      config={node.config as Record<string, unknown>}
      language={language}
    />
  );
}

function TriggerFields({
  workflowId,
  nodes,
  config,
  language,
}: {
  workflowId: string;
  nodes: TriggerNode[];
  config: Record<string, unknown>;
  language: Language;
}) {
  const qc = useQueryClient();
  const hintId = React.useId();
  const [scope, setScope] = React.useState(config.owner_scope === "workspace" ? "workspace" : "self");
  const [sources, setSources] = React.useState(() => asArray<string>(config.source_types).join(", "));
  const [title, setTitle] = React.useState(typeof config.title_contains === "string" ? config.title_contains : "");
  const save = useMutation({
    mutationFn: () => {
      const next: Record<string, unknown> = { ...config, owner_scope: scope };
      delete next.source_types;
      delete next.title_contains;
      const sourceTypes = sources.split(",").map((item) => item.trim()).filter(Boolean);
      if (sourceTypes.length) next.source_types = sourceTypes;
      if (title.trim()) next.title_contains = title.trim();
      return latticeApi.updateWorkflow(workflowId, {
        nodes: nodes.map((node) => (isBrainEventTrigger(node) ? { ...node, config: next } : node)),
      });
    },
    onSuccess: async (result) => {
      if (result.ok) await qc.invalidateQueries({ queryKey: ["workflowDefinitions"] });
    },
  });

  return (
    <details className="mt-3 border-t border-border pt-2 text-sm">
      <summary className="cursor-pointer font-medium">{t(language, "act.brainTrigger.summary")}</summary>
      <form
        className="mt-3 grid max-w-md gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <label className="grid gap-1 text-xs text-muted-foreground">
          {t(language, "act.brainTrigger.scope")}
          <select
            className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
            value={scope}
            onChange={(event) => setScope(event.target.value)}
          >
            <option value="self">{t(language, "act.brainTrigger.scope.self")}</option>
            <option value="workspace">{t(language, "act.brainTrigger.scope.workspace")}</option>
          </select>
        </label>
        <div className="grid gap-1">
          <label className="grid gap-1 text-xs text-muted-foreground">
            {t(language, "act.brainTrigger.sources")}
            <Input value={sources} aria-describedby={hintId} onChange={(event) => setSources(event.target.value)} />
          </label>
          <p id={hintId} className="text-xs text-muted-foreground">{t(language, "act.brainTrigger.sourcesHint")}</p>
        </div>
        <label className="grid gap-1 text-xs text-muted-foreground">
          {t(language, "act.brainTrigger.title")}
          <Input value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <Button type="submit" variant="outline" className="justify-self-start" disabled={save.isPending}>
          {t(language, "act.brainTrigger.save")}
        </Button>
        {save.data ? (
          <p role="status" className={`text-xs ${save.data.ok ? "text-success" : "text-warning"}`}>
            {save.data.ok ? t(language, "act.brainTrigger.saved") : save.data.error || t(language, "ui.status.unavailable")}
          </p>
        ) : null}
      </form>
    </details>
  );
}
