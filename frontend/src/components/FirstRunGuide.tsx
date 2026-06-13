import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Cpu, Download, Layers3, Library, PlayCircle, SlidersHorizontal, UserCircle, Users } from "lucide-react";
import { latticeApi } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/store/appStore";
import { go } from "@/routes";
import { asArray } from "@/lib/utils";

function readDismissed() {
  try {
    return localStorage.getItem("lattice.onboarding.dismissed") === "true";
  } catch {}
  return false;
}

export function FirstRunGuide() {
  const [dismissed, setDismissed] = React.useState(readDismissed);
  const mode = useAppStore((state) => state.mode);
  const profile = useQuery({ queryKey: ["profile"], queryFn: latticeApi.profile });
  const workspace = useQuery({ queryKey: ["workspaceOs"], queryFn: latticeApi.workspaceOs });
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const recs = useQuery({ queryKey: ["modelRecommendations", "local_mlx"], queryFn: () => latticeApi.modelRecommendations("local_mlx") });
  if (dismissed) return null;

  const profileData = (profile.data?.data || {}) as Record<string, unknown>;
  const workspaceData = (workspace.data?.data || {}) as Record<string, unknown>;
  const registry = (workspaceData.workspace_registry || {}) as Record<string, unknown>;
  const modelData = (models.data?.data || {}) as Record<string, unknown>;
  const recommendationData = ((recs.data?.data as Record<string, unknown> | undefined)?.recommendations || {}) as Record<string, unknown>;
  const currentModel = String(modelData.current || "");
  const loadedModels = asArray(modelData.loaded);
  const topPick = recommendationData.top_pick as Record<string, unknown> | undefined;
  const compatProfiles = asArray<Record<string, unknown>>(modelData.compat_profiles);
  const readyProfile = compatProfiles.some((item) => item.chat_compatible || item.quality_status === "ok" || item.quality_status === "degraded");

  const steps = [
    { label: "Login", done: Boolean(profileData.email), icon: UserCircle, action: "account" },
    { label: "Workspace Selection", done: Boolean(registry.active_workspace || workspaceData.active_workspace), icon: Users, action: "workspace-admin" },
    { label: "Environment Analysis", done: recs.isSuccess, icon: Cpu, action: "models" },
    { label: "Model Recommendation", done: Boolean(topPick || currentModel), icon: Library, action: "models" },
    { label: "Model Installation", done: Boolean(currentModel || loadedModels.length), icon: Download, action: "models" },
    { label: "Model Validation", done: Boolean(readyProfile || currentModel || loadedModels.length), icon: PlayCircle, action: "models" },
    { label: "Mode Selection", done: Boolean(mode), icon: SlidersHorizontal, action: "settings" },
    { label: "Brain Usage", done: true, icon: Layers3, action: "knowledge-graph" },
  ];

  return (
    <section className="mb-4 rounded-md border border-border bg-card p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm text-primary"><CheckCircle2 className="h-4 w-4" /> First-run setup</div>
          <h2 className="mt-1 text-xl font-semibold">Start with your workspace, recommended model, and mode.</h2>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Lattice guides setup in this order: login, workspace, environment analysis, model recommendation, installation, validation, mode selection, then Brain usage.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="muted">{mode === "basic" ? "Basic mode" : `${mode} mode`}</Badge>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              try { localStorage.setItem("lattice.onboarding.dismissed", "true"); } catch {}
              setDismissed(true);
            }}
          >
            Dismiss
          </Button>
        </div>
      </div>
      <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
        {steps.map((step) => {
          const Icon = step.icon;
          return (
            <button
              key={step.label}
              onClick={() => go(step.action)}
              className="min-h-28 rounded-md border border-border bg-background p-3 text-left hover:bg-muted"
            >
              <Icon className="h-4 w-4 text-primary" />
              <div className="mt-2 text-sm font-medium">{step.label}</div>
              <Badge className="mt-2" variant={step.done ? "success" : "warning"}>{step.done ? "ready" : "next"}</Badge>
            </button>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => go("account")}>Login</Button>
        <Button size="sm" variant="outline" onClick={() => go("workspace-admin")}>Choose Workspace</Button>
        <Button size="sm" variant="outline" onClick={() => go("models")}>Set Up Model</Button>
        <Button size="sm" variant="outline" onClick={() => go("settings")}>Choose Mode</Button>
      </div>
    </section>
  );
}
