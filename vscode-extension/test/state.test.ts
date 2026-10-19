import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { FLAGS, parseCsv, readState, summarizeGrades, listArchivedWeeks } from "../src/state";

const GRADING_REPO = path.resolve(__dirname, "..", "..", "..");

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "dsa-grading-test-"));
}

function write(file: string, text = ""): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
}

const HEADER =
  "student_id,compiled,tests_passed,tests_total,score,max_score,uncapped_score,score_cap,passed_tests,failed_tests,failure_details,notes";

test("every flag marker is still written by the script that owns it", () => {
  // grade.py and check_lateness.py own these strings. If one is reworded
  // there, step 6's counts would silently drop to 0 - this catches it.
  for (const f of FLAGS) {
    const source = fs.readFileSync(path.join(GRADING_REPO, f.source), "utf8");
    assert.ok(source.includes(f.marker), `${f.marker} not found in ${f.source}`);
  }
});

test("csv cells with commas, quotes and newlines survive", () => {
  const rows = parseCsv('a,b\r\n"x, y","say ""hi""\nthere"\n');
  assert.deepEqual(rows, [
    ["a", "b"],
    ["x, y", 'say "hi"\nthere'],
  ]);
});

test("grades.csv is summarised into the flags step 6 shows", () => {
  const csv = [
    HEADER,
    "10000001,yes,11,11,11,11,11,,,,,",
    "10000002,no,0,0,0,0,0,,,,,\"COMPILE ERROR: Foo.java:3: error: cannot find symbol | ...\"",
    '10000003,yes,9,11,4.5,11,9,50%,,,,"SCORE CAPPED AT 50%: no source; MANUAL REVIEW: mixed source"',
    "10000004,yes,0,0,0,11,0,,,,,",
    '10000005,yes,11,11,0,11,11,0%,,,,"TA OVERRIDE: worked, but edited a given method"',
  ].join("\n");
  const s = summarizeGrades(csv);
  assert.equal(s.rows, 5);
  assert.equal(s.compiled, 4);
  assert.equal(s.fullMarks, 1);
  assert.equal(s.maxScore, 11);
  assert.equal(s.flags.compile, 1);
  assert.equal(s.flags.capped, 1);
  assert.equal(s.flags.manual, 1);
  assert.equal(s.flags.override, 1);
  assert.equal(s.flags.zeroTests, 1);
  assert.equal(s.flags.structure, 0);
});

test("archived weeks are listed in order, questions numerically", () => {
  const dir = tmp();
  write(path.join(dir, "restore-week.sh"));
  for (const p of ["week10/q2/tests/x", "week10/q1/tests/x", "week02/q10/x", "week02/q9/x", "notaweek/q1/x", "week03/readme/x"]) {
    write(path.join(dir, p));
  }
  assert.deepEqual(listArchivedWeeks(dir), [
    { week: "week02", questions: ["q9", "q10"] },
    { week: "week10", questions: ["q1", "q2"] },
  ]);
});

test("a fresh grading folder reads as nothing done", () => {
  const g = tmp();
  write(path.join(g, "grade.py"));
  write(path.join(g, "tests", ".gitkeep"));
  write(path.join(g, "submissions", ".gitkeep"));
  const s = readState(g, path.join(g, "no-such-setup"));
  assert.equal(s.gradingDirOk, true);
  assert.deepEqual(s.tests.javaFiles, []);
  assert.equal(s.submissions.count, 0, ".gitkeep is not a submission");
  assert.equal(s.results.grades, undefined);
  assert.equal(s.setupRepo.ok, false);
});

test("a graded week is read back from disk", () => {
  const g = tmp();
  write(path.join(g, "grade.py"));
  write(path.join(g, "tests", "TestTree.java"));
  write(path.join(g, "tests", "rubric.json"), "{}");
  write(path.join(g, "tests", "starter", "Tree.java"));
  write(path.join(g, "tests", "report_config.json"), JSON.stringify({ assignment_title: "Lab Tree Q2" }));
  write(path.join(g, "submissions", "10000001.jar"));
  write(path.join(g, "submissions", "10000002.zip"));
  write(path.join(g, "results", "grades.csv"), HEADER + "\n10000001,yes,1,1,1,1,1,,,,,\n");
  write(path.join(g, "results", "mcvScore.csv"), "10000001,1\n");
  const s = readState(g, g);
  assert.deepEqual(s.tests.javaFiles, ["TestTree.java"]);
  assert.equal(s.tests.rubric, true);
  assert.equal(s.tests.structure, false);
  assert.equal(s.tests.starter, true);
  assert.equal(s.tests.assignmentTitle, "Lab Tree Q2");
  assert.equal(s.submissions.count, 2);
  assert.equal(s.results.grades?.summary?.rows, 1);
  assert.ok(s.results.scores);
  assert.equal(s.results.scoresBackup, undefined);
});

test("a half-written report_config.json still counts as present", () => {
  const g = tmp();
  write(path.join(g, "tests", "report_config.json"), "{ not json");
  const s = readState(g, g);
  assert.equal(s.tests.reportConfig, true);
  assert.equal(s.tests.assignmentTitle, undefined);
});
