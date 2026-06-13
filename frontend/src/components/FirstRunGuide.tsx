import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, Cpu, Download, Layers3, Library, PlayCircle, SlidersHorizontal, UserCircle, Users } from "lucide-react";
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
  const completed = steps.filter((step) => step.done).length;
  const nextStep = steps.find((step) => !step.done) || steps[steps.length - 1];
  const progress = Math.round((completed / steps.length) * 100);

  return (
    <section className="mb-6 overflow-hidden rounded-xl border border-border bg-card/86 shadow-[0_24px_80px_hsl(0_0%_0%/0.24)]">
      <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-4">
          <div className="page-kicker"><CheckCircle2 className="h-4 w-4" /> First run</div>
          <h2 className="text-3xl font-semibold tracking-normal">Set up your Digital Brain without guessing.</h2>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            Start with a workspace, pick a local model, then begin capturing knowledge. Lattice will keep setup actions explicit.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => go(nextStep.action)}>{nextStep.done ? "Open Brain" : `Continue: ${nextStep.label}`}</Button>
            <Button size="sm" variant="outline" onClick={() => go("models")}>Model Setup</Button>
            <Button size="sm" variant="outline" onClick={() => go("knowledge-graph")}>Open Brain Map</Button>
          </div>
        </div>
        <div className="rounded-lg border border-border bg-background/54 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold">{completed} of {steps.length} ready</div>
              <div className="mt-1 text-xs text-muted-foreground">{mode === "basic" ? "Basic mode" : `${mode} mode`}</div>
            </div>
            <Badge variant={progress === 100 ? "success" : "warning"}>{progress}%</Badge>
          </div>
          <div className="mt-4 h-2 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary" style={{ width: `${progress}%` }} />
          </div>
          <Button
            variant="outline"
            size="sm"
            className="mt-4 w-full"
            onClick={() => {
              try { localStorage.setItem("lattice.onboarding.dismissed", "true"); } catch {}
              setDismissed(true);
            }}
          >
            Hide guide
          </Button>
        </div>
      </div>
      <div className="grid gap-2 border-t border-border bg-background/28 p-3 md:grid-cols-2 xl:grid-cols-4">
        {steps.map((step) => {
          const Icon = step.icon;
          return (
            <button
              key={step.label}
              onClick={() => go(step.action)}
              className="group min-h-28 rounded-lg border border-border bg-card/72 p-3 text-left transition hover:-translate-y-0.5 hover:bg-muted/70"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="grid h-8 w-8 place-items-center rounded-md bg-primary/12 text-primary"><Icon className="h-4 w-4" /></span>
                <Badge variant={step.done ? "success" : "warning"}>{step.done ? "ready" : step.label === nextStep.label ? "next" : "later"}</Badge>
              </div>
              <div className="mt-3 text-sm font-semibold">{step.label}</div>
              <div className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
                {step.done ? "Review" : "Start"} <ArrowRight className="h-3 w-3 transition group-hover:translate-x-0.5" />
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
