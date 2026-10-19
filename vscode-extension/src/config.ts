// Where everything is, from the dsaGrading.* settings with sensible defaults
// for the usual layout:
//
//   java-auto-grader/          <- the folder the TA opens in VS Code
//     grading/                 <- this repo (grade.py)
//     TA-grading-setup/        <- the private archive (restore-week.sh)

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { Paths } from "./steps";

const GIT_BASH = "C:\\Program Files\\Git\\bin\\bash.exe";

const isFile = (p: string) => {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
};

const expandHome = (p: string) => (p.startsWith("~") ? path.join(os.homedir(), p.slice(1)) : p);

/** The dsaGrading.gradingDir setting, resolved, or undefined when it is empty. */
export function configuredGradingDir(): string | undefined {
  const configured = vscode.workspace.getConfiguration("dsaGrading").get<string>("gradingDir", "").trim();
  return configured ? path.resolve(expandHome(configured)) : undefined;
}

/**
 * The grader folder, in order: the setting; the folder the TA picked (or the
 * workspace search found) last time, if it is still there; a workspace folder
 * holding grade.py, or its grading/ child. Undefined when none of those works -
 * the controller then searches the whole workspace (see discover.ts).
 */
export function findGradingDir(remembered?: string): string | undefined {
  const configured = configuredGradingDir();
  if (configured) return configured;
  if (remembered && isFile(path.join(remembered, "grade.py"))) return remembered;
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const root = folder.uri.fsPath;
    if (isFile(path.join(root, "grade.py"))) return root;
    if (isFile(path.join(root, "grading", "grade.py"))) return path.join(root, "grading");
  }
  return undefined;
}

export function resolvePaths(gradingDir: string): Paths {
  const cfg = vscode.workspace.getConfiguration("dsaGrading");
  const setting = (key: string) => cfg.get<string>(key, "").trim();
  return {
    gradingDir,
    setupRepoDir: setting("setupRepoDir")
      ? path.resolve(expandHome(setting("setupRepoDir")))
      : path.resolve(gradingDir, "..", "TA-grading-setup"),
    materialsDir: setting("materialsDir") ? path.resolve(expandHome(setting("materialsDir"))) : "",
    python: setting("python") || "python",
    bash: setting("bash") || (process.platform === "win32" && isFile(GIT_BASH) ? GIT_BASH : "bash"),
  };
}
