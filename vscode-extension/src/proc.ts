// Runs one grader command as a child process. No vscode import: runner.ts wires
// it to the terminal and progress UI, and the end-to-end check drives it from
// plain node against a scratch copy of the grader.
//
// Always spawn(exe, args[]) with shell:false. Every argument - a zip path with
// spaces, a deadline, a class list - reaches the script as exactly one argv
// entry, so nothing a TA types or a file is named can be read as a command.

import { spawn, spawnSync, ChildProcess } from "child_process";
import { LineSplitter } from "./progress";

export interface Command {
  exe: string;
  args: string[];
  cwd: string;
}

export interface RunHandle {
  /** Resolves with the exit code (null if killed by a signal). */
  done: Promise<number | null>;
  kill(): void;
}

export interface RunCallbacks {
  /** Raw output as it arrives, for the terminal. */
  onData?(text: string): void;
  /** Each complete line, for progress parsing. */
  onLine?(line: string): void;
}

/** How a command is shown in the terminal header and in "copy command". */
export function displayCommand(cmd: Command): string {
  const quote = (s: string) => (/^[\w./:\\=-]+$/.test(s) ? s : `"${s.replace(/"/g, '\\"')}"`);
  return [cmd.exe, ...cmd.args].map(quote).join(" ");
}

export function runCommand(cmd: Command, cb: RunCallbacks = {}): RunHandle {
  const child: ChildProcess = spawn(cmd.exe, cmd.args, {
    cwd: cmd.cwd,
    shell: false,
    windowsHide: true,
    // Unbuffered so grade.py's per-student lines arrive as they happen rather
    // than in one burst at exit; UTF-8 so non-ASCII file names don't crash a
    // print on a cp1252 console.
    env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" },
  });
  const splitter = new LineSplitter();
  const handle = (chunk: Buffer) => {
    const text = chunk.toString("utf8");
    cb.onData?.(text);
    for (const line of splitter.push(text)) cb.onLine?.(line);
  };
  child.stdout?.on("data", handle);
  child.stderr?.on("data", handle);

  const done = new Promise<number | null>((resolve) => {
    child.on("error", (err) => {
      cb.onData?.(`\nCould not start ${cmd.exe}: ${err.message}\n`);
      resolve(-1);
    });
    child.on("close", (code) => {
      for (const line of splitter.flush()) cb.onLine?.(line);
      resolve(code);
    });
  });

  return {
    done,
    kill() {
      if (child.pid === undefined || child.exitCode !== null) return;
      if (process.platform === "win32") {
        // child.kill() would stop python but leave the javac/java processes
        // grade.py started running in the background; /T takes the whole tree.
        spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
      } else {
        child.kill("SIGTERM");
      }
    },
  };
}
