// The walkthrough page for one step, in the editor area. A single panel that
// moves between steps (Back / Continue / the stepper), rather than one tab per
// step, so the editor never fills up with old pages.

import * as vscode from "vscode";
import { Controller } from "./controller";
import { webviewHtml } from "./html";
import { STEPS } from "./steps";

type Message =
  | { type: "ready" }
  | { type: "goto"; index: number }
  | { type: "setValue"; id: string; value: string }
  | { type: "pickZips" }
  | { type: "run"; action: string }
  | { type: "cancel" }
  | { type: "copy"; text: string; what?: string }
  | { type: "tick"; value: boolean }
  | { type: "link"; id: string }
  | { type: "openTerminal" }
  | { type: "chooseGradingDir" };

export class StepPanel implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private index = 0;
  private readonly subs: vscode.Disposable[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly controller: Controller,
  ) {
    this.subs.push(
      controller.onDidChange(() => this.post()),
      controller.onProgress((running) => this.panel?.webview.postMessage({ type: "progress", running })),
    );
  }

  show(index: number): void {
    this.index = Math.max(0, Math.min(STEPS.length - 1, index));
    if (!this.panel) {
      this.panel = vscode.window.createWebviewPanel("dsaGrading.step", "DSA Grading", vscode.ViewColumn.Active, {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")],
      });
      this.panel.iconPath = vscode.Uri.joinPath(this.extensionUri, "media", "icon.svg");
      this.panel.webview.html = webviewHtml(this.panel.webview, this.extensionUri, "panel.js", "DSA Grading");
      this.panel.webview.onDidReceiveMessage((m: Message) => this.onMessage(m), undefined, this.subs);
      this.panel.onDidDispose(() => (this.panel = undefined), undefined, this.subs);
    } else {
      this.panel.reveal(undefined, false);
    }
    this.post();
  }

  private post(): void {
    if (!this.panel) return;
    this.panel.title = `${this.index + 1}. ${STEPS[this.index].title}`;
    this.panel.webview.postMessage({ type: "model", model: this.controller.panelModel(this.index) });
  }

  private onMessage(m: Message): void {
    const c = this.controller;
    switch (m.type) {
      case "ready":
        this.post();
        break;
      case "goto":
        this.show(m.index);
        break;
      case "setValue":
        void c.setValue(m.id, m.value);
        break;
      case "pickZips":
        void c.pickZips();
        break;
      case "run":
        void c.run(this.index, m.action);
        break;
      case "cancel":
        c.cancel();
        break;
      case "copy":
        void c.copy(m.text, m.what);
        break;
      case "tick":
        void c.tick(STEPS[this.index].id, m.value);
        break;
      case "link":
        void c.openLink(this.index, m.id);
        break;
      case "openTerminal":
        c.openTerminal();
        break;
      case "chooseGradingDir":
        void c.chooseGradingDir();
        break;
    }
  }

  dispose(): void {
    this.panel?.dispose();
    for (const s of this.subs) s.dispose();
  }
}
