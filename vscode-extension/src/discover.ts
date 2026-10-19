// Which folders in a workspace are the grader. No vscode import
// (test/discover.test.ts runs it on temp folders).
//
// The extension wakes up for any grade.py anywhere in the workspace, so a TA
// who opens a parent folder (all of their Projects, say) still gets it - but
// then it has to find the grader rather than assume it sits at the top or in
// grading/. A grade.py alone is a weak signal (a scratch copy, an unrelated
// project's script), so a candidate must look like the whole tool: grade.py
// beside tests/ and lib/.

import * as fs from "fs";
import * as path from "path";

const isFile = (p: string) => {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
};

const isDir = (p: string) => {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
};

export function looksLikeGradingDir(dir: string): boolean {
  return isFile(path.join(dir, "grade.py")) && isDir(path.join(dir, "tests")) && isDir(path.join(dir, "lib"));
}

/**
 * From the grade.py files a workspace search found, the folders that are a
 * whole grader - shallowest first, so the one a TA most likely means is at the
 * top of the picker, then alphabetically for a stable order.
 */
export function graderCandidates(gradePyFiles: string[]): string[] {
  const dirs = [...new Set(gradePyFiles.map((f) => path.dirname(path.resolve(f))))].filter(looksLikeGradingDir);
  const depth = (p: string) => p.split(/[\\/]+/).filter(Boolean).length;
  return dirs.sort((a, b) => depth(a) - depth(b) || a.localeCompare(b));
}
