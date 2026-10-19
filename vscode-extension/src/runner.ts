// Runs one step's command with its output in VS Code's terminal panel and its
// progress in a notification (with Cancel) and on the step page.
//
// The output goes to a Pseudoterminal: a real terminal tab the extension
// writes into, so the TA scrolls, searches and copies the full output as in
// any terminal, while every line also passes through progress.ts. One tab
// ("DSA Grading") is reused for every run, each under its own header.

import { execFile } from "child_process";
import * as vscode from "vscode";
import { Command, displayCommand, runCommand } from "./proc";
import { GradeSummary, parseGradeSummary, parseProgress, Progress } from "./progress";

class OutputPty implements vscode.Pseudoterminal {
  private readonly writeEmitter = new vscode.EventEmitter<string>();
  private readonly closeEmitter = new vscode.EventEmitter<void>();
  readonly onDidWrite = this.writeEmitter.event;
  readonly onDidClose = this.closeEmitter.event;
  private opened = false;
  private pending: string[] = [];
  /** Ctrl+C in the terminal cancels the running command. */
  onInterrupt?: () => void;

  open(): void {
    this.opened = true;
    for (const p of this.pending) this.writeEmitter.fire(p);
    this.pending = [];
  }

  close(): void {
    this.opened = false;
  }

  handleInput(data: string): void {
    if (data === "\x03") this.onInterrupt?.();
  }

  write(text: string): void {
    const out = text.replace(/\r?\n/g, "\r\n");
    if (this.opened) this.writeEmitter.fire(out);
    else this.pending.push(out);
  }
}

export interface RunResult {
  code: number | null;
  seconds: number;
  cancelled: boolean;
  summary?: GradeSummary;
}

export class Runner implements vscode.Disposable {
  private pty?: OutputPty;
  private terminal?: vscode.Terminal;
  private readonly closeSub: vscode.Disposable;
  private kill?: () => void;
  private cancelled = false;

  constructor() {
    this.closeSub = vscode.window.onDidCloseTerminal((t) => {
      if (t === this.terminal) {
        this.terminal = undefined;
        this.pty = undefined;
      }
    });
  }

  get busy(): boolean {
    return this.kill !== undefined;
  }

  cancel(): void {
    this.cancelled = true;
    this.kill?.();
  }

  private output(): OutputPty {
    if (!this.pty || !this.terminal) {
      this.pty = new OutputPty();
      this.terminal = vscode.window.createTerminal({
        name: "DSA Grading",
        pty: this.pty,
        iconPath: new vscode.ThemeIcon("checklist"),
      });
    }
    this.pty.onInterrupt = () => this.cancel();
    this.terminal.show(true);
    return this.pty;
  }

  async run(title: string, cmd: Command, onProgress: (p: Progress) => void): Promise<RunResult> {
    const out = this.output();
    const bold = (s: string) => `\x1b[1m${s}\x1b[0m`;
    const dim = (s: string) => `\x1b[2m${s}\x1b[0m`;
    out.write(`\n${bold(`━━ ${title}`)}  ${dim(new Date().toLocaleTimeString())}\n${dim(`$ ${displayCommand(cmd)}`)}\n${dim(`  in ${cmd.cwd}`)}\n\n`);

    this.cancelled = false;
    const started = Date.now();
    let summary: GradeSummary | undefined;

    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title, cancellable: true },
      async (progress, token) => {
        let shown = 0;
        const handle = runCommand(cmd, {
          onData: (text) => out.write(text),
          onLine: (line) => {
            summary = parseGradeSummary(line) ?? summary;
            const p = parseProgress(line);
            if (!p) return;
            progress.report({ increment: Math.max(0, p.percent - shown), message: `${p.done}/${p.total} - ${p.message}` });
            shown = Math.max(shown, p.percent);
            onProgress(p);
          },
        });
        this.kill = () => handle.kill();
        token.onCancellationRequested(() => this.cancel());
        try {
          return await handle.done;
        } finally {
          this.kill = undefined;
        }
      },
    );

    const seconds = Math.round((Date.now() - started) / 100) / 10;
    const status = this.cancelled
      ? "\x1b[33mcancelled\x1b[0m"
      : result === 0
        ? "\x1b[32mfinished\x1b[0m"
        : `\x1b[31mexited with code ${result}\x1b[0m`;
    out.write(`\n${bold("━━")} ${status} ${dim(`after ${seconds}s`)}\n`);
    return { code: result, seconds, cancelled: this.cancelled, summary };
  }

  dispose(): void {
    this.cancel();
    this.closeSub.dispose();
  }
}

/** The major version `javac -version` reports, or undefined if javac isn't on PATH. */
export function javacMajor(): Promise<number | undefined> {
  return new Promise((resolve) => {
    execFile("javac", ["-version"], { windowsHide: true, timeout: 20000 }, (err, stdout, stderr) => {
      const m = /javac\s+(\d+)/.exec(`${stdout}${stderr}`);
      resolve(err && !m ? undefined : m ? Number(m[1]) : undefined);
    });
  });
}
