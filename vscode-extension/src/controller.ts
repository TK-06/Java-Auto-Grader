// The one place that knows the current week: what the TA has filled in and
// ticked (kept in workspaceState, so it survives closing VS Code), what disk
// says, and what is running. Both webviews only render what this hands them
// and send back what the TA clicked.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { configuredGradingDir, findGradingDir, resolvePaths } from "./config";
import { graderCandidates, looksLikeGradingDir } from "./discover";
import { unwritable } from "./locks";
import { GradeSummary } from "./progress";
import { Runner, javacMajor } from "./runner";
import { readState } from "./state";
import { Ctx, Fact, Paths, STEPS, StepView, Values, actionProblem, currentStepIndex, describeStep } from "./steps";

interface Session {
  values: Values;
  ticks: Record<string, boolean>;
  /** When the TA last reset the checklist; see Ctx.since. */
  resetAt?: number;
}

export interface RunState {
  stepIndex: number;
  label: string;
  percent?: number;
  message?: string;
}

export interface LastRun {
  actionId: string;
  label: string;
  code: number | null;
  seconds: number;
  cancelled: boolean;
  summary?: GradeSummary;
  at: number;
}

export interface Problem {
  ok: false;
  problem: string;
  /** Offer the "Choose grader folder..." button. */
  canChoose?: boolean;
}

export interface ListModel {
  ok: true;
  weekLabel: string;
  gradingDir: string;
  current: number;
  steps: { index: number; id: string; title: string; summary: string; done: boolean; detected?: boolean; ticked: boolean; facts: Fact[] }[];
  running?: RunState;
}

export interface PanelModel {
  ok: true;
  weekLabel: string;
  current: number;
  steps: { index: number; title: string; done: boolean }[];
  step: StepView;
  running?: RunState;
  last?: LastRun;
  /** What restore-week.sh can restore, for the week/question pickers. */
  archived: { week: string; questions: string[] }[];
}

const SESSION_KEY = "dsaGrading.session";
/** The grader folder picked (or found by the workspace search) for this
 *  workspace. workspaceState, not a setting: choosing a folder never writes a
 *  .vscode/settings.json into the TA's project. */
const GRADER_KEY = "dsaGrading.graderDir";

/** Folders never worth searching for the grader: dependencies, build output,
 *  and the grader's own data folders (a scratch copy of the tool in there is
 *  not the one to run). */
const SEARCH_EXCLUDE = "**/{node_modules,.git,out,build_tmp,submissions,results,scratchPad}/**";

const exists = (p: string) => {
  try {
    fs.statSync(p);
    return true;
  } catch {
    return false;
  }
};

export class Controller implements vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<void>();
  private readonly progressed = new vscode.EventEmitter<RunState>();
  private readonly graderChanged = new vscode.EventEmitter<string | undefined>();
  readonly onDidChange = this.changed.event;
  readonly onProgress = this.progressed.event;
  /** The grader folder changed (found, picked, or the setting edited). */
  readonly onDidChangeGrader = this.graderChanged.event;
  private searching = false;
  private lastGrader?: string;
  /** Ask the panel to show a step (set by extension.ts). */
  openStep: (index: number) => void = () => undefined;

  running?: RunState;
  private readonly lastRuns: Record<string, LastRun> = {};

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly runner: Runner,
  ) {}

  // ------------------------------------------------------------- state

  paths(): Paths | undefined {
    const dir = findGradingDir(this.context.workspaceState.get<string>(GRADER_KEY));
    return dir ? resolvePaths(dir) : undefined;
  }

  private session(): Session {
    const s = this.context.workspaceState.get<Session>(SESSION_KEY);
    return { values: { ...(s?.values ?? {}) }, ticks: { ...(s?.ticks ?? {}) }, resetAt: s?.resetAt };
  }

  private async save(s: Session): Promise<void> {
    await this.context.workspaceState.update(SESSION_KEY, s);
  }

  ctx(): Ctx | Problem {
    const paths = this.paths();
    if (!paths) {
      if (this.searching) return { ok: false, problem: "Looking for the grader in this workspace…" };
      return {
        ok: false,
        canChoose: true,
        problem:
          "No grader found in this workspace – no folder here holds grade.py together with tests/ and lib/. " +
          "Choose the grader folder, or open the folder that contains grading/.",
      };
    }
    const state = readState(paths.gradingDir, paths.setupRepoDir);
    if (!state.gradingDirOk) {
      return {
        ok: false,
        canChoose: true,
        problem: `grade.py not found in ${paths.gradingDir}${configuredGradingDir() ? " – the dsaGrading.gradingDir setting points there" : ""}.`,
      };
    }
    const session = this.session();
    return { state, paths, values: session.values, since: session.resetAt, exists };
  }

  private weekLabel(ctx: Ctx): string {
    const { week, question } = ctx.values;
    if (week && question) return `Week ${week.replace(/^week0?/, "")} · Q${question}`;
    return ctx.state.tests.assignmentTitle ?? (ctx.state.tests.javaFiles.length ? ctx.state.tests.javaFiles.join(", ") : "No week loaded");
  }

  listModel(): ListModel | Problem {
    const ctx = this.ctx();
    if ("ok" in ctx) return ctx;
    const ticks = this.session().ticks;
    return {
      ok: true,
      weekLabel: this.weekLabel(ctx),
      gradingDir: ctx.paths.gradingDir,
      current: currentStepIndex(ctx, ticks),
      steps: STEPS.map((_, i) => {
        const v = describeStep(i, ctx, ticks);
        return { index: i, id: v.id, title: v.title, summary: v.summary, done: v.done, detected: v.detected, ticked: v.ticked, facts: v.facts.slice(0, 3) };
      }),
      running: this.running,
    };
  }

  panelModel(index: number): PanelModel | Problem {
    const ctx = this.ctx();
    if ("ok" in ctx) return ctx;
    const ticks = this.session().ticks;
    const step = describeStep(index, ctx, ticks);
    return {
      ok: true,
      weekLabel: this.weekLabel(ctx),
      current: currentStepIndex(ctx, ticks),
      steps: STEPS.map((s, i) => ({ index: i, title: s.title, done: !!ticks[s.id] || s.done?.(ctx) === true })),
      step,
      running: this.running,
      last: this.lastRuns[step.id],
      archived: ctx.state.setupRepo.weeks,
    };
  }

  currentIndex(): number {
    const ctx = this.ctx();
    return "ok" in ctx ? 0 : currentStepIndex(ctx, this.session().ticks);
  }

  refresh(): void {
    const now = this.paths()?.gradingDir;
    if (now !== this.lastGrader) {
      this.lastGrader = now;
      this.graderChanged.fire(now);
    }
    this.changed.fire();
  }

  // ------------------------------------------------- finding the grader

  private async searchWorkspace(): Promise<string[]> {
    const files = await vscode.workspace.findFiles("**/grade.py", SEARCH_EXCLUDE, 200);
    return graderCandidates(files.map((u) => u.fsPath));
  }

  private async remember(dir: string): Promise<void> {
    await this.context.workspaceState.update(GRADER_KEY, dir);
    this.refresh();
  }

  /**
   * At startup: when the setting, the remembered folder and the quick checks
   * all come up empty, search the workspace. One grader found is used (and
   * remembered); several means asking which, since guessing wrong would run
   * the scripts in the wrong folder.
   */
  async discover(): Promise<void> {
    if (this.paths()) return this.refresh();
    this.searching = true;
    this.changed.fire();
    let found: string[] = [];
    try {
      found = await this.searchWorkspace();
    } finally {
      this.searching = false;
    }
    if (found.length === 1) return this.remember(found[0]);
    this.refresh();
    if (found.length > 1) {
      const choice = await vscode.window.showInformationMessage(
        `DSA Grading found ${found.length} grader folders in this workspace. Which one do you grade with?`,
        "Choose…",
      );
      if (choice) await this.chooseGradingDir(found);
    }
  }

  /** The "Choose grader folder..." command: every grader in the workspace, plus Browse. */
  async chooseGradingDir(known?: string[]): Promise<void> {
    const found = known ?? (await this.searchWorkspace());
    const current = this.paths()?.gradingDir;
    const roots = (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
    const shown = (dir: string) => {
      const root = roots.find((r) => dir.startsWith(r));
      return root ? path.join(path.basename(root), path.relative(root, dir)) : dir;
    };
    type Item = vscode.QuickPickItem & { dir?: string };
    const items: Item[] = [
      ...found.map((dir) => ({ label: `$(folder) ${shown(dir)}`, description: dir === current ? "current" : undefined, detail: dir, dir })),
      { label: "$(folder-opened) Browse…", detail: "Pick a folder holding grade.py, tests/ and lib/" },
    ];
    const pick = await vscode.window.showQuickPick(items, {
      title: "Choose the grader folder",
      placeHolder: found.length ? "The folder holding grade.py, tests/ and lib/" : "No grader found in this workspace – browse for it",
    });
    if (!pick) return;
    let dir = pick.dir;
    if (!dir) {
      const picked = await vscode.window.showOpenDialog({
        canSelectFiles: false,
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: "Use this grader folder",
        defaultUri: current ? vscode.Uri.file(current) : vscode.workspace.workspaceFolders?.[0]?.uri,
      });
      dir = picked?.[0]?.fsPath;
      if (!dir) return;
      if (!looksLikeGradingDir(dir)) {
        vscode.window.showWarningMessage(`${dir} doesn't hold grade.py, tests/ and lib/, so it isn't the grader folder.`);
        return;
      }
    }
    if (configuredGradingDir()) {
      // The setting always wins, so a choice stored beside it would do nothing.
      await vscode.workspace.getConfiguration("dsaGrading").update("gradingDir", dir, vscode.ConfigurationTarget.Workspace);
    }
    await this.remember(dir);
  }

  // ----------------------------------------------------------- actions

  async setValue(id: string, value: string): Promise<void> {
    const s = this.session();
    if ((s.values[id] ?? "") === value) return;
    s.values[id] = value;
    await this.save(s);
    this.changed.fire();
  }

  async tick(stepId: string, value: boolean): Promise<void> {
    const s = this.session();
    s.ticks[stepId] = value;
    await this.save(s);
    this.changed.fire();
  }

  /** "Reset progress": start the checklist over for the next question. Only the
   *  extension's own memory changes - the files on disk are never touched. They
   *  stay where they are but stop counting as done (see Ctx.since). */
  async newWeek(): Promise<void> {
    const ok = await vscode.window.showWarningMessage(
      "Reset progress for a new question? This clears every tick and remembered input (zips, deadline, week, question), " +
        "and treats the tests, submissions and results now on disk as the previous question's, so the checklist starts again at step 1. " +
        "No file is changed or deleted.",
      { modal: true },
      "Reset progress",
    );
    if (!ok) return;
    await this.save({ values: {}, ticks: {}, resetAt: Date.now() });
    for (const k of Object.keys(this.lastRuns)) delete this.lastRuns[k];
    this.changed.fire();
    this.openStep(0);
  }

  async pickZips(): Promise<void> {
    const downloads = path.join(os.homedir(), "Downloads");
    const picked = await vscode.window.showOpenDialog({
      canSelectMany: true,
      canSelectFiles: true,
      canSelectFolders: false,
      filters: { "MyCourseVille export": ["zip"] },
      defaultUri: exists(downloads) ? vscode.Uri.file(downloads) : undefined,
      openLabel: "Use these zips",
      title: "Pick every part of the MyCourseVille bulk export",
    });
    if (picked?.length) await this.setValue("zips", picked.map((u) => u.fsPath).sort().join("\n"));
  }

  async copy(text: string, what = "Copied"): Promise<void> {
    await vscode.env.clipboard.writeText(text);
    vscode.window.setStatusBarMessage(`$(check) ${what} to the clipboard`, 2500);
  }

  openTerminal(): void {
    const paths = this.paths();
    const terminal = vscode.window.createTerminal({
      name: "Grading shell",
      cwd: paths?.gradingDir,
      iconPath: new vscode.ThemeIcon("terminal"),
    });
    terminal.show();
  }

  async openLink(stepIndex: number, linkId: string): Promise<void> {
    const ctx = this.ctx();
    if ("ok" in ctx) return;
    const link = STEPS[stepIndex]?.links?.find((l) => l.id === linkId);
    const target = link?.target(ctx);
    if (!link || !target) {
      vscode.window.showWarningMessage(linkId === "materials" ? "Set dsaGrading.materialsDir to the professor's materials folder first." : "Nothing to open yet.");
      return;
    }
    const uri = vscode.Uri.file(target);
    if (link.kind === "file") await vscode.commands.executeCommand("vscode.open", uri);
    else if (link.kind === "reveal") await vscode.commands.executeCommand("revealFileInOS", uri);
    else await vscode.env.openExternal(uri);
  }

  cancel(): void {
    this.runner.cancel();
  }

  async run(stepIndex: number, actionId: string): Promise<void> {
    if (this.runner.busy) {
      vscode.window.showInformationMessage("Another step is still running - wait for it, or cancel it first.");
      return;
    }
    const ctx = this.ctx();
    if ("ok" in ctx) {
      vscode.window.showErrorMessage(ctx.problem);
      return;
    }
    const step = STEPS[stepIndex];
    const action = step?.actions.find((a) => a.id === actionId);
    if (!step || !action) return;

    const problem = actionProblem(step, action, ctx);
    if (problem) {
      vscode.window.showWarningMessage(problem);
      return;
    }
    const locked = unwritable(action.outputs?.(ctx) ?? []);
    if (locked.length) {
      const names = locked.map((f) => path.relative(ctx.paths.gradingDir, f)).join(", ");
      vscode.window.showWarningMessage(
        `${names} can't be written to - most likely it is open in Excel. Close it (choose Don't save), then run "${action.label}" again; the script would stop straight away.`,
      );
      return;
    }

    const question = action.confirm?.(ctx);
    if (question && (await vscode.window.showWarningMessage(question, { modal: true }, "Run")) !== "Run") return;

    if (action.needsJdk25) {
      const major = await javacMajor();
      if (major !== 25) {
        const said = major === undefined ? "javac was not found on PATH" : `javac -version reports ${major}`;
        const go = await vscode.window.showWarningMessage(
          `${said}. The grader needs JDK 25: with another JDK, compliant submissions can fail for the wrong reason.`,
          { modal: true },
          "Run anyway",
        );
        if (go !== "Run anyway") return;
      }
    }

    const cmd = action.build(ctx);
    const title = `Step ${stepIndex + 1}: ${action.label}`;
    this.running = { stepIndex, label: action.label };
    this.changed.fire();
    let result;
    try {
      result = await this.runner.run(title, cmd, (p) => {
        this.running = { stepIndex, label: action.label, percent: p.percent, message: `${p.done}/${p.total} · ${p.message}` };
        this.progressed.fire(this.running);
      });
    } finally {
      this.running = undefined;
    }
    this.lastRuns[step.id] = { actionId, label: action.label, ...result, at: Date.now() };
    this.changed.fire();

    if (result.cancelled) return;
    if (result.code === 0) {
      const next = stepIndex + 1 < STEPS.length ? stepIndex + 1 : undefined;
      const choice = await vscode.window.showInformationMessage(
        `${title} finished in ${result.seconds}s. Read its output in the DSA Grading terminal.`,
        ...(next !== undefined ? [`Go to step ${next + 1}`] : []),
      );
      if (choice && next !== undefined) this.openStep(next);
    } else {
      vscode.window.showErrorMessage(`${title} exited with code ${result.code} - the DSA Grading terminal has the full output.`);
    }
  }

  dispose(): void {
    this.changed.dispose();
    this.progressed.dispose();
    this.graderChanged.dispose();
  }
}
