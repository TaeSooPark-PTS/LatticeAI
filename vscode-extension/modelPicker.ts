import * as vscode from "vscode";
import { ConnectAIClient, ModelInfo } from "../client";

export class ModelPicker {
  constructor(private client: ConnectAIClient) {}

  async show(): Promise<string | undefined> {
    // 서버 살아있는지 체크
    const alive = await this.client.isAlive();
    if (!alive) {
      const action = await vscode.window.showErrorMessage(
        "Connect AI: Server not running. Start it first.",
        "How to start"
      );
      if (action === "How to start") {
        vscode.env.openExternal(
          vscode.Uri.parse("https://github.com/your-repo/connect-ai-mlx#quick-start")
        );
      }
      return undefined;
    }

    const { recommended, loaded, current } = await this.client.listModels();

    type Item = vscode.QuickPickItem & { modelId: string };

    const items: Item[] = [];

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
    items.push({ label: "── Recommended (will download if needed) ──", kind: vscode.QuickPickItemKind.Separator, modelId: "" });

    const tagIcons: Record<string, string> = {
      coding: "$(code)",
      general: "$(hubot)",
      reasoning: "$(lightbulb)",
    };

    for (const m of recommended) {
      if (loaded.includes(m.id)) continue; // 위에 이미 표시됨
      items.push({
        label: `${tagIcons[m.tag] ?? "$(cloud)"} ${shortName(m.id)}`,
        description: `${m.size} · ${m.tag}`,
        detail: m.id,
        modelId: m.id,
      });
    }

    // 직접 입력
    items.push({ label: "── Custom ──", kind: vscode.QuickPickItemKind.Separator, modelId: "" });
    items.push({
      label: "$(add) Enter HuggingFace model ID...",
      description: "any mlx-community model",
      detail: "__custom__",
      modelId: "__custom__",
    });

    const pick = await vscode.window.showQuickPick(items, {
      title: "Connect AI MLX — Select Model",
      placeHolder: "Search models...",
      matchOnDescription: true,
      matchOnDetail: true,
    });

    if (!pick || !pick.modelId) return undefined;

    if (pick.modelId === "__custom__") {
      return vscode.window.showInputBox({
        prompt: "Enter HuggingFace model ID",
        placeHolder: "e.g. mlx-community/Llama-3.1-8B-Instruct-4bit",
        validateInput: (v) => (v.includes("/") ? null : "Format: org/model-name"),
      });
    }

    return pick.modelId;
  }
}

function shortName(id: string): string {
  return id.split("/").pop()?.replace(/-4bit$/, "") ?? id;
}
