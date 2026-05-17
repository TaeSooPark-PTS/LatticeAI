import * as vscode from "vscode";
import { LatticeAIClient } from "./client";
import * as fs from "fs";
import * as path from "path";

export class ChatPanel {
  static currentPanel: ChatPanel | undefined;
  private static readonly viewType = "ltcai.chat";
  private readonly _panel: vscode.WebviewPanel;
  private _disposables: vscode.Disposable[] = [];
  private static _client: LatticeAIClient;
  private static _pendingMessage: string | undefined;

  static createOrShow(extensionUri: vscode.Uri, client: LatticeAIClient) {
    ChatPanel._client = client;
    const column = vscode.window.activeTextEditor ? vscode.ViewColumn.Beside : vscode.ViewColumn.One;

    if (ChatPanel.currentPanel) {
      ChatPanel.currentPanel._panel.reveal(column);
      if (ChatPanel._pendingMessage) {
        ChatPanel.currentPanel._panel.webview.postMessage({ type: "prefill", text: ChatPanel._pendingMessage });
        ChatPanel._pendingMessage = undefined;
      }
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      ChatPanel.viewType,
      "Lattice AI MLX",
      column,
      { enableScripts: true, retainContextWhenHidden: true }
    );

    ChatPanel.currentPanel = new ChatPanel(panel, extensionUri);
  }

  static sendMessage(text: string) {
    if (ChatPanel.currentPanel) {
      ChatPanel.currentPanel._panel.webview.postMessage({ type: "prefill", text });
    } else {
      ChatPanel._pendingMessage = text;
    }
  }

  private constructor(panel: vscode.WebviewPanel, private readonly _extensionUri: vscode.Uri) {
    this._panel = panel;
    this._panel.webview.html = this._getHtml();

    this._panel.onDidDispose(() => this.dispose(), null, this._disposables);

    this._panel.webview.onDidReceiveMessage(
      async (msg) => {
        const client = ChatPanel._client as any;
        if (msg.type === "send") await this._handleChat(msg.text, msg.context);
        if (msg.type === "getContext") {
          const editor = vscode.window.activeTextEditor;
          const context = editor ? `File: ${editor.document.fileName}\n\n${editor.document.getText().slice(0, 4000)}` : undefined;
          this._panel.webview.postMessage({ type: "context", context });
        }
        
        // 모델 목록 요청
        if (msg.type === "fetchModels") {
          try {
            const models = await client.listModels();
            const current = await client.getCurrentModel();
            this._panel.webview.postMessage({ type: "models", models, current });
          } catch (e) {}
        }

        // 모델 변경 요청
        if (msg.type === "loadModel") {
          try {
            await client.loadModel(msg.modelId);
            const current = await client.getCurrentModel();
            const models = await client.listModels();
            this._panel.webview.postMessage({ type: "models", models, current });
          } catch (err: any) {
            this._panel.webview.postMessage({ type: "error", message: "모델 로드 실패: " + err.message });
          }
        }
      },
      null,
      this._disposables
    );
  }

  private _getHtml(): string {
    // ← 여기서 html 파일 읽기
    const htmlPath = path.join(this._extensionUri.fsPath, "chatPanel.html");

    try {
      return fs.readFileSync(htmlPath, "utf-8");
    } catch (err) {
      console.error("chatPanel.html 파일을 찾을 수 없습니다:", htmlPath);
      return `<h1>chatPanel.html 파일을 찾을 수 없습니다.</h1>`;
    }
  }

  private async _handleChat(text: string, context?: string) {
    this._panel.webview.postMessage({ type: "startAssistant" });
    try {
      const client = ChatPanel._client as any;
      for await (const chunk of client.streamGenerate(text, context)) {
        this._panel.webview.postMessage({ type: "chunk", chunk });
      }
    } catch (err: any) {
      this._panel.webview.postMessage({ type: "error", message: err?.message || "Unknown error" });
    }
    this._panel.webview.postMessage({ type: "done" });
  }

  dispose() {
    ChatPanel.currentPanel = undefined;
    this._panel.dispose();
    this._disposables.forEach(d => d.dispose());
  }
}