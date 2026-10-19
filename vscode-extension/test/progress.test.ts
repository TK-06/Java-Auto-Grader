import { test } from "node:test";
import * as assert from "node:assert/strict";
import { parseProgress, parseGradeSummary, LineSplitter } from "../src/progress";

// Lines copied from grade.py's own print() formats (grade.py main loop and
// the archive-unpacking loop), with synthetic IDs.

test("a graded-student line advances the bar", () => {
  const p = parseProgress("[3/40] 10000003: compiled, 9/11 tests passed (score 9/11)");
  assert.deepEqual(p, {
    done: 3,
    total: 40,
    percent: 8,
    phase: "grading",
    message: "10000003: compiled, 9/11 tests passed (score 9/11)",
  });
});

test("a failed-student line counts too", () => {
  const p = parseProgress("[40/40] 10000040: COMPILE ERROR (score 0) - cannot find symbol");
  assert.equal(p?.percent, 100);
  assert.equal(p?.phase, "grading");
});

test("the indented unpacking counter is its own phase", () => {
  const p = parseProgress("  [12/40] unpacking 10000012.jar ...");
  assert.equal(p?.phase, "unpacking");
  assert.equal(p?.done, 12);
});

test("ordinary output is not progress", () => {
  for (const line of [
    "Done. Wrote 40 rows to results/grades.csv and results/mcvScore.csv",
    "  rubric:      tests/rubric.json",
    "[a/b] nope",
    "[5/0] division by zero is not progress",
    "[9/4] done beyond total is not progress",
  ]) {
    assert.equal(parseProgress(line), undefined, line);
  }
});

test("the final summary line is parsed", () => {
  const s = parseGradeSummary("  compiled: 38/40   average score: 8.10   0-tests-found: 0   timeouts: 1");
  assert.deepEqual(s, { compiled: 38, rows: 40, average: 8.1, zeroTests: 0, timeouts: 1 });
});

test("lines split across chunks are reassembled, CRLF included", () => {
  const s = new LineSplitter();
  assert.deepEqual(s.push("[1/2] 1000"), []);
  assert.deepEqual(s.push("0001: compiled\r\n[2/2] 10"), ["[1/2] 10000001: compiled"]);
  assert.deepEqual(s.push("000002: compiled\n"), ["[2/2] 10000002: compiled"]);
  assert.deepEqual(s.push("tail without newline"), []);
  assert.deepEqual(s.flush(), ["tail without newline"]);
  assert.deepEqual(s.flush(), []);
});
