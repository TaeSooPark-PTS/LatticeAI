import * as vscode from "vscode";
import { LatticeAIClient } from "./client";
import { parseModelRecommendation } from "./surface";

export class ModelPicker {
  constructor(private client: LatticeAIClient) {}

  async show(): Promise<string | undefined> {
    // 서버 살아있는지 체크
    const alive = await this.client.isAlive();
    if (!alive) {
      const action = await vscode.window.showErrorMessage(
        "Lattice AI: Server not running. Start it first.",
        "How to start"
      );
      if (action === "How to start") {
        vscode.env.openExternal(
          vscode.Uri.parse("https://github.com/your-repo/ltcai#quick-start")
        );
      }
      return undefined;
    }

    const { recommended, loaded, current } = await this.client.listModels();

    // The web Library view explains *why* a model suits this machine. The
    // picker used to list a catalogue with no reasoning, which SURFACE_PARITY
    // recorded as ◐. The recommendation is read from the server, never
    // recomputed here — an unavailable scan simply means no banner.
    let advice: ReturnType<typeof parseModelRecommendation> = null;
    try {
      advice = parseModelRecommendation(await this.client.setupScan());
    } catch {
      advice = null;
    }

    type Item = vscode.QuickPickItem & { modelId: string };

    const items: Item[] = [];

    if (advice) {
      items.push({
        label: "── Recommended for this machine ──",
        kind: vscode.QuickPickItemKind.Separator,
        modelId: "",
      });
      items.push({
        label: `$(star-full) ${shortName(advice.modelId)}`,
        description: advice.runtime ? `${advice.runtime} · recommended here` : "recommended here",
        detail: advice.rationale.join(" · ") || advice.modelId,
        modelId: advice.modelId,
      });
    }

    // 이미 로드된 모델 (즉시 전환 가능)
    if (loaded.length > 0) {
      items.push({ label: "── Loaded (instant switch) ──", kind: vscode.QuickPickItemKind.Separator, modelId: "" });
      for (const id of loaded) {
        items.push({
          label: `$(check) ${shortName(id)}`,
          description: id === current ? "● active" : "loaded",
          detail: id,
          modelId: id,
        });
      }
    }

    // 추천 모델 (다운로드/로드 필요)
    items.push({ label: "── Recommended multimodal models ──", kind: vscode.QuickPickItemKind.Separator, modelId: "" });

    const tagIcons: Record<string, string> = {
      coding: "$(code)",
      general: "$(hubot)",
      reasoning: "$(lightbulb)",
    };

    for (const m of recommended) {
      if (loaded.includes(m.id)) continue; // 위에 이미 표시됨
      const sourceDetail = [
        m.source_country,
        m.source_company,
        m.execution_method,
        m.internet_requirement,
        m.model_name ?? m.name,
      ].filter(Boolean).join(" · ");
      items.push({
        label: `${tagIcons[m.tag] ?? "$(cloud)"} ${shortName(m.id)}`,
        description: `${m.size} · ${m.tag}`,
        detail: sourceDetail || m.id,
        modelId: m.id,
      });
    }

    // 직접 입력
    items.push({ label: "── Custom ──", kind: vscode.QuickPickItemKind.Separator, modelId: "" });
    items.push({
      label: "$(add) Enter HuggingFace model ID...",
      description: "multimodal model only",
      detail: "__custom__",
      modelId: "__custom__",
    });

    const pick = await vscode.window.showQuickPick(items, {
      title: "Lattice AI — Select Model",
      placeHolder: "Search models...",
      matchOnDescription: true,
      matchOnDetail: true,
    });

    if (!pick || !pick.modelId) return undefined;

    if (pick.modelId === "__custom__") {
      return vscode.window.showInputBox({
        prompt: "Enter HuggingFace model ID",
        placeHolder: "e.g. mlx-community/gemma-4-12b-it-4bit",
        validateInput: (v) => (v.includes("/") ? null : "Format: org/model-name"),
      });
    }

    return pick.modelId;
  }
}

function shortName(id: string): string {
  return id.split("/").pop()?.replace(/-4bit$/, "") ?? id;
}
