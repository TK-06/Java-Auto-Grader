// Entry point. Activates only in a workspace that contains grade.py (see
// activationEvents in package.json), wires the checklist view, the step page,
// the commands and a file watcher that keeps both in step with disk.

import * as vscode from "vscode";
import { Controller } from "./controller";
import { Runner } from "./runner";
import { StepPanel } from "./stepPanel";
import { StepsViewProvider } from "./stepsView";

const HINT_KEY = "dsaGrading.rightSideBarHintShown";

export function activate(context: vscode.ExtensionContext): void {
  const runner = new Runner();
  const controller = new Controller(context, runner);
  const panel = new StepPanel(context.extensionUri, controller);
  const view = new StepsViewProvider(context.extensionUri, controller);
  controller.openStep = (index) => panel.show(index);

  context.subscriptions.push(
    runner,
    controller,
    panel,
    view,
    vscode.window.registerWebviewViewProvider(StepsViewProvider.viewId, view),
    vscode.commands.registerCommand("dsaGrading.openCurrentStep", () => panel.show(controller.currentIndex())),
    vscode.commands.registerCommand("dsaGrading.refresh", () => controller.refresh()),
    vscode.commands.registerCommand("dsaGrading.openTerminal", () => controller.openTerminal()),
    vscode.commands.registerCommand("dsaGrading.newWeek", () => controller.newWeek()),
    vscode.commands.registerCommand("dsaGrading.moveToRight", moveToRight),
    vscode.workspace.onDidChangeConfiguration((e) => e.affectsConfiguration("dsaGrading") && controller.refresh()),
  );

  // tests/, submissions/ and results/ change under the extension's feet - a
  // script it ran, a file the TA edited, an agent in the terminal. Debounced:
  // extraction writes a hundred files in a burst.
  const paths = controller.paths();
  if (paths) {
    let timer: NodeJS.Timeout | undefined;
    const poke = () => {
      clearTimeout(timer);
      timer = setTimeout(() => controller.refresh(), 400);
    };
    for (const dir of ["tests", "submissions", "results"]) {
      const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(paths.gradingDir), `${dir}/**`));
      watcher.onDidCreate(poke);
      watcher.onDidChange(poke);
      watcher.onDidDelete(poke);
      context.subscriptions.push(watcher);
    }
    context.subscriptions.push({ dispose: () => clearTimeout(timer) });
  }

  if (vscode.workspace.getConfiguration("dsaGrading").get<boolean>("openOnStartup", true) && paths) {
    panel.show(controller.currentIndex());
  }

  if (!context.globalState.get<boolean>(HINT_KEY)) {
    void context.globalState.update(HINT_KEY, true);
    void vscode.window
      .showInformationMessage(
        "DSA Grading: the step checklist works best in the right side bar. Move it there now? VS Code remembers where you put it.",
        "Move it",
        "Not now",
      )
      .then((choice) => {
        if (choice === "Move it") void moveToRight();
      });
  }
}

/** Focus the checklist, then VS Code's own "Move View" picker - pick Secondary Side Bar. */
async function moveToRight(): Promise<void> {
  try {
    await vscode.commands.executeCommand(`${StepsViewProvider.viewId}.focus`);
    await vscode.commands.executeCommand("workbench.action.moveFocusedView");
  } catch {
    vscode.window.showInformationMessage("Drag the DSA Grading icon from the activity bar onto the right side bar.");
  }
}

export function deactivate(): void {
  // Everything is in context.subscriptions.
}
