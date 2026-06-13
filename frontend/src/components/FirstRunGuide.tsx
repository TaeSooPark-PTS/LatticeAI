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
    { label: "Make it yours", done: Boolean(profileData.email), icon: UserCircle, action: "account", detail: "Sign in or keep a local profile." },
    { label: "Choose a space", done: Boolean(registry.active_workspace || workspaceData.active_workspace), icon: Users, action: "workspace-admin", detail: "Decide where memories belong." },
    { label: "Meet your Mac", done: recs.isSuccess, icon: Cpu, action: "models", detail: "Let Lattice inspect what can run locally." },
    { label: "Pick a brain", done: Boolean(topPick || currentModel), icon: Library, action: "models", detail: "Use the recommended local model." },
    { label: "Install locally", done: Boolean(currentModel || loadedModels.length), icon: Download, action: "models", detail: "Download only with explicit consent." },
    { label: "Try a question", done: Boolean(readyProfile || currentModel || loadedModels.length), icon: PlayCircle, action: "chat", detail: "Confirm the model can answer." },
    { label: "Set the pace", done: Boolean(mode), icon: SlidersHorizontal, action: "settings", detail: "Stay Calm or switch deeper." },
    { label: "Explore memory", done: true, icon: Layers3, action: "knowledge-graph", detail: "Open the living map." },
  ];
  const completed = steps.filter((step) => step.done).length;
  const nextStep = steps.find((step) => !step.done) || steps[steps.length - 1];
  const progress = Math.round((completed / steps.length) * 100);

  return (
    <section className="arrival-panel" aria-label="First 10 minutes">
      <div className="arrival-copy">
        <div className="page-kicker"><CheckCircle2 className="h-4 w-4" /> First 10 minutes</div>
        <h2>Build your Digital Brain without guessing.</h2>
        <p>
          Start with a space, let Lattice recommend a private local model, then add the first pieces of knowledge.
          Every step keeps the next action visible.
        </p>
        <div className="arrival-actions">
          <Button onClick={() => go(nextStep.action)}>{nextStep.done ? "Open memory map" : `Continue: ${nextStep.label}`}</Button>
          <Button variant="outline" onClick={() => go("models")}>Set up model</Button>
          <Button variant="ghost" onClick={() => {
            try { localStorage.setItem("lattice.onboarding.dismissed", "true"); } catch {}
            setDismissed(true);
          }}>
            Hide
          </Button>
        </div>
      </div>
      <div className="journey-panel">
        <div className="journey-head">
          <div>
            <div className="text-sm font-semibold">{completed} of {steps.length} ready</div>
            <div className="text-xs text-muted-foreground">{mode === "basic" ? "Calm mode" : `${mode} mode`}</div>
          </div>
          <Badge variant={progress === 100 ? "success" : "warning"}>{progress}%</Badge>
        </div>
        <div className="journey-progress"><span style={{ width: `${progress}%` }} /></div>
        <div className="journey-steps">
          {steps.map((step) => {
            const Icon = step.icon;
            return (
              <button key={step.label} onClick={() => go(step.action)} className="journey-step">
                <span className="journey-icon"><Icon className="h-4 w-4" /></span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold">{step.label}</span>
                  <span className="block truncate text-xs text-muted-foreground">{step.detail}</span>
                </span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
