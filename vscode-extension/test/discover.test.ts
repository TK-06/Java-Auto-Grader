import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { graderCandidates, looksLikeGradingDir } from "../src/discover";

function tree(files: string[]): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dsa-grading-discover-"));
  for (const f of files) {
    const p = path.join(root, f);
    if (f.endsWith("/")) {
      fs.mkdirSync(p, { recursive: true });
    } else {
      fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.writeFileSync(p, "");
    }
  }
  return root;
}

test("a grader is grade.py beside tests/ and lib/", () => {
  const root = tree(["full/grade.py", "full/tests/", "full/lib/", "lone/grade.py", "notests/grade.py", "notests/lib/"]);
  assert.equal(looksLikeGradingDir(path.join(root, "full")), true);
  assert.equal(looksLikeGradingDir(path.join(root, "lone")), false, "a stray grade.py is not the tool");
  assert.equal(looksLikeGradingDir(path.join(root, "notests")), false);
  assert.equal(looksLikeGradingDir(path.join(root, "missing")), false);
});

test("the opened-a-parent-folder case: the grader two levels down is found", () => {
  // Projects/ opened in VS Code; the grader lives in java-auto-grader/grading.
  const root = tree([
    "java-auto-grader/grading/grade.py",
    "java-auto-grader/grading/tests/",
    "java-auto-grader/grading/lib/",
    "netCen/scripts/grade.py",
  ]);
  const found = graderCandidates([
    path.join(root, "java-auto-grader", "grading", "grade.py"),
    path.join(root, "netCen", "scripts", "grade.py"),
  ]);
  assert.deepEqual(found, [path.join(root, "java-auto-grader", "grading")]);
});

test("several graders come back shallowest first, each once", () => {
  const root = tree(["b/deep/er/grade.py", "b/deep/er/tests/", "b/deep/er/lib/", "a/grade.py", "a/tests/", "a/lib/", "c/grade.py", "c/tests/", "c/lib/"]);
  const g = (...p: string[]) => path.join(root, ...p, "grade.py");
  const found = graderCandidates([g("b", "deep", "er"), g("c"), g("a"), g("a")]);
  assert.deepEqual(found, [path.join(root, "a"), path.join(root, "c"), path.join(root, "b", "deep", "er")]);
});

test("nothing that looks like the tool means no candidates", () => {
  const root = tree(["x/grade.py"]);
  assert.deepEqual(graderCandidates([path.join(root, "x", "grade.py")]), []);
  assert.deepEqual(graderCandidates([]), []);
});
