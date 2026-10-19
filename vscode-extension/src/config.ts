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

/** The grader folder, or undefined when this workspace has no grade.py. */
export function findGradingDir(): string | undefined {
  const configured = vscode.workspace.getConfiguration("dsaGrading").get<string>("gradingDir", "").trim();
  if (configured) return path.resolve(expandHome(configured));
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
