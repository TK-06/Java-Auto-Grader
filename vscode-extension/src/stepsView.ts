// The checklist in the side bar. Clicking a step opens its page in the editor
// area; the list follows whatever the controller says changed.

import * as vscode from "vscode";
import { Controller } from "./controller";
import { webviewHtml } from "./html";

export class StepsViewProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  static readonly viewId = "dsaGrading.steps";
  private view?: vscode.WebviewView;
  private readonly subs: vscode.Disposable[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly controller: Controller,
  ) {
    this.subs.push(
      controller.onDidChange(() => this.post()),
      controller.onProgress((running) => this.view?.webview.postMessage({ type: "progress", running })),
    );
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")] };
    view.webview.html = webviewHtml(view.webview, this.extensionUri, "view.js", "DSA Grading steps");
    view.webview.onDidReceiveMessage((msg) => this.onMessage(msg), undefined, this.subs);
    view.onDidChangeVisibility(() => view.visible && this.post(), undefined, this.subs);
  }

  private post(): void {
    this.view?.webview.postMessage({ type: "model", model: this.controller.listModel() });
  }

  private onMessage(msg: { type: string; index?: number }): void {
    switch (msg.type) {
      case "ready":
        this.post();
        break;
      case "open":
        if (typeof msg.index === "number") this.controller.openStep(msg.index);
        break;
      case "newWeek":
        void this.controller.newWeek();
        break;
      case "openTerminal":
        this.controller.openTerminal();
        break;
    }
  }

  dispose(): void {
    for (const s of this.subs) s.dispose();
  }
}
