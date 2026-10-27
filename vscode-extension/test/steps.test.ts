import { test } from "node:test";
import * as assert from "node:assert/strict";
import { Ctx, Paths, STEPS, Values, actionProblem, currentStepIndex, describeStep, normalizeDeadline } from "../src/steps";
import { GradingState } from "../src/state";

const PATHS: Paths = {
  gradingDir: "C:\\g\\grading",
  setupRepoDir: "C:\\g\\TA-grading-setup",
  materialsDir: "",
  python: "python",
  bash: "C:\\Program Files\\Git\\bin\\bash.exe",
};

function state(over: Partial<GradingState> = {}): GradingState {
  return {
    gradingDir: PATHS.gradingDir,
    gradingDirOk: true,
    tests: { javaFiles: [], rubric: false, structure: false, manualReview: false, starter: false, reportConfig: false, newestMtimeMs: 0 },
    submissions: { count: 0, newestMtimeMs: 0 },
    results: {},
    setupRepo: { dir: PATHS.setupRepoDir, ok: true, weeks: [{ week: "week10", questions: ["q1", "q2"] }] },
    ...over,
  };
}

/** A grades.csv with `rows` graded rows, as state.ts would read it. */
const grades = (mtimeMs: number, rows = 1) => ({
  mtimeMs,
  summary: { rows, compiled: rows, fullMarks: 0, averageScore: 0, maxScore: 1, flags: {} as never },
});

const ZIPS = "C:\\Users\\ta\\Downloads\\part 1.zip\nC:\\Users\\ta\\Downloads\\part2.zip";

function ctx(values: Values, st = state(), existing: string[] = ZIPS.split("\n")): Ctx {
  return { state: st, paths: PATHS, values, exists: (p) => existing.includes(p) };
}

const step = (id: string) => STEPS.find((s) => s.id === id)!;
const action = (stepId: string, actionId: string) => step(stepId).actions.find((a) => a.id === actionId)!;

test("restore runs restore-week.sh through bash with forward-slash paths", () => {
  const c = ctx({ week: "week10", question: "2" });
  assert.equal(actionProblem(step("setup"), action("setup", "restore"), c), undefined);
  assert.deepEqual(action("setup", "restore").build(c), {
    exe: PATHS.bash,
    args: ["C:/g/TA-grading-setup/restore-week.sh", "week10", "q2", "C:/g/grading"],
    cwd: PATHS.setupRepoDir,
  });
});

test("restore is blocked for a week that was never archived, and for bad input", () => {
  const notArchived = actionProblem(step("setup"), action("setup", "restore"), ctx({ week: "week11", question: "1" }));
  assert.match(notArchived ?? "", /not archived/);
  assert.match(actionProblem(step("setup"), action("setup", "restore"), ctx({ week: "wk10", question: "1" })) ?? "", /week05/);
  assert.match(actionProblem(step("setup"), action("setup", "restore"), ctx({ week: "week10", question: "0" })) ?? "", /number/);
});

test("restore asks before replacing a loaded tests/", () => {
  const loaded = state({ tests: { ...state().tests, javaFiles: ["TestBST02.java"] } });
  assert.match(action("setup", "restore").confirm!(ctx({ week: "week10", question: "1" }, loaded)) ?? "", /TestBST02\.java/);
  assert.equal(action("setup", "restore").confirm!(ctx({ week: "week10", question: "1" })), undefined);
});

test("extract passes every zip as its own argument, spaces and all", () => {
  const c = ctx({ zips: ZIPS, question: "2" });
  assert.deepEqual(action("extract", "extract").build(c), {
    exe: "python",
    args: ["extract_submissions.py", "C:\\Users\\ta\\Downloads\\part 1.zip", "C:\\Users\\ta\\Downloads\\part2.zip", "--question", "2"],
    cwd: PATHS.gradingDir,
  });
  assert.deepEqual(action("extract", "dry").build(c).args.slice(-1), ["--dry-run"]);
});

test("extract is blocked until the zips exist", () => {
  const missing = actionProblem(step("extract"), action("extract", "extract"), ctx({ zips: ZIPS, question: "2" }, state(), []));
  assert.match(missing ?? "", /Not found/);
  const none = actionProblem(step("extract"), action("extract", "extract"), ctx({ question: "2" }));
  assert.match(none ?? "", /step 3/);
});

test("grade needs tests and submissions, and warns before dropping overrides", () => {
  const g = action("grade", "grade");
  assert.match(actionProblem(step("grade"), g, ctx({})) ?? "", /step 2/);
  const ready = state({ tests: { ...state().tests, javaFiles: ["TestX.java"] }, submissions: { count: 3, newestMtimeMs: 1 } });
  assert.equal(actionProblem(step("grade"), g, ctx({}, ready)), undefined);
  assert.deepEqual(g.build(ctx({}, ready)), { exe: "python", args: ["grade.py"], cwd: PATHS.gradingDir });
  assert.equal(g.needsJdk25, true);
  const withOverride = state({
    ...ready,
    results: {
      grades: {
        mtimeMs: 2,
        summary: { rows: 3, compiled: 3, fullMarks: 1, averageScore: 1, maxScore: 1, flags: { override: 2 } as never },
      },
    },
  });
  assert.match(g.confirm!(ctx({}, withOverride)) ?? "", /2 TA OVERRIDE/);
});

test("the deadline reaches check_lateness.py in its exact format", () => {
  assert.equal(normalizeDeadline("2026-10-20T23:59"), "2026-10-20T23:59:00");
  assert.equal(normalizeDeadline("2026-10-20T23:59:30"), "2026-10-20T23:59:30");
  for (const bad of ["2026-09-06:T23:59:59", "2026-02-30T10:00", "2026-10-20 23:59", "2026-10-20T24:00", ""]) {
    assert.equal(normalizeDeadline(bad), undefined, bad);
  }
  const graded = state({ results: { grades: grades(1) } });
  const c = ctx({ zips: ZIPS, question: "1", deadline: "2026-10-20T23:59" }, graded);
  assert.equal(actionProblem(step("late"), action("late", "late"), c), undefined);
  assert.deepEqual(action("late", "late").build(c).args, [
    "check_lateness.py",
    "C:\\Users\\ta\\Downloads\\part 1.zip",
    "C:\\Users\\ta\\Downloads\\part2.zip",
    "--deadline",
    "2026-10-20T23:59:00",
    "--question",
    "1",
  ]);
});

test("audits build only the flags that were filled in, and reject junk names", () => {
  const starter = state({ tests: { ...state().tests, starter: true } });
  const dupe = action("validate", "dupe");
  assert.match(dupe.blocked!(ctx({}, starter)) ?? "", /classes/);
  assert.deepEqual(dupe.build(ctx({ dupeWhole: " BankQueue , Node " }, starter)).args, ["dupe_check.py", "--whole-file", "BankQueue,Node"]);
  assert.deepEqual(dupe.build(ctx({ dupeClasses: "BST", dupeMethod: "height" }, starter)).args, [
    "dupe_check.py",
    "--classes",
    "BST",
    "--method",
    "height",
  ]);
  assert.match(dupe.blocked!(ctx({ dupeClasses: "BST" }, starter)) ?? "", /method/);

  const given = action("validate", "given");
  assert.deepEqual(given.build(ctx({}, starter)).args, ["audit_given_code.py", "--allow", "", "--diff"]);
  assert.deepEqual(given.build(ctx({ allow: "height, makeBalancedTree", givenClasses: "BST" }, starter)).args, [
    "audit_given_code.py",
    "--allow",
    "height,makeBalancedTree",
    "--classes",
    "BST",
    "--diff",
  ]);
  assert.match(given.blocked!(ctx({ allow: "rm -rf /" }, starter)) ?? "", /Java names/);
  assert.match(given.blocked!(ctx({})) ?? "", /starter/);

  const forbidden = action("validate", "forbidden");
  assert.match(forbidden.blocked!(ctx({ entry: "removeRange" })) ?? "", /classes/);
  assert.deepEqual(
    forbidden.build(ctx({ entry: "removeRange", forbiddenClasses: "StackArray", forbidden: "pop, push" })).args,
    ["audit_forbidden.py", "--entry", "removeRange", "--classes", "StackArray", "--forbidden", "pop,push"],
  );
});

test("the archive command is shown, never runnable", () => {
  // Every action, with every input filled in so none is blocked.
  const everything = ctx(
    {
      week: "week10",
      question: "1",
      zips: ZIPS,
      deadline: "2026-10-20T23:59",
      dupeWhole: "BankQueue",
      entry: "removeRange",
      forbiddenClasses: "StackArray",
      forbidden: "pop",
    },
    state({
      tests: { ...state().tests, javaFiles: ["T.java"], starter: true, reportConfig: true },
      submissions: { count: 1, newestMtimeMs: 1 },
      results: { grades: grades(2) },
    }),
  );
  let built = 0;
  for (const s of STEPS) {
    for (const a of s.actions) {
      assert.equal(actionProblem(s, a, everything), undefined, `${s.id}/${a.id} should be runnable here`);
      const c = a.build(everything);
      const all = [c.exe, ...c.args].join(" ");
      assert.doesNotMatch(all, /archive-week|\bgit\b/, `${s.id}/${a.id} must not run git`);
      built++;
    }
  }
  assert.equal(built, STEPS.reduce((n, s) => n + s.actions.length, 0));
  const copy = step("setup").copyCommands!(ctx({ week: "week10", question: "1" }));
  assert.match(copy[0].text, /archive-week\.sh week10 q1/);
});

test("the current step is the first one not done, and ticks count", () => {
  const graded = state({
    tests: { ...state().tests, javaFiles: ["T.java"] },
    submissions: { count: 2, newestMtimeMs: 10 },
    results: { grades: grades(20) },
  });
  const c = ctx({ zips: ZIPS }, graded);
  assert.equal(STEPS[currentStepIndex(c, {})].id, "materials");
  assert.equal(STEPS[currentStepIndex(c, { materials: true })].id, "validate");
  const view = describeStep(STEPS.findIndex((s) => s.id === "grade"), c, {});
  assert.equal(view.detected, true);
  assert.equal(view.done, true);
  assert.equal(view.actions[0].disabled, undefined);
  assert.match(view.actions[0].command ?? "", /^python grade\.py$/);
});

test("an empty grades.csv (a run cancelled on a fresh week) is not a result", () => {
  const st = state({
    tests: { ...state().tests, javaFiles: ["T.java"], reportConfig: true },
    submissions: { count: 2, newestMtimeMs: 10 },
    results: { grades: grades(20, 0) },
  });
  const c = ctx({ zips: ZIPS, question: "1", deadline: "2026-10-20T23:59" }, st);
  assert.equal(describeStep(STEPS.findIndex((s) => s.id === "grade"), c, {}).detected, false);
  assert.match(actionProblem(step("late"), action("late", "late"), c) ?? "", /no graded rows/);
  assert.match(actionProblem(step("report"), action("report", "report"), c) ?? "", /no graded rows/);
});

test("step 7 is done once the .bak is at least as new as grades.csv, and undone by a re-grade", () => {
  const at = (gradesMs: number, bakMs?: number) =>
    describeStep(
      STEPS.findIndex((s) => s.id === "late"),
      ctx({}, state({ results: { grades: grades(gradesMs), scores: { mtimeMs: gradesMs }, scoresBackup: bakMs === undefined ? undefined : { mtimeMs: bakMs } } })),
      {},
    ).detected;
  assert.equal(at(100), false, "no late check yet");
  assert.equal(at(100, 100), true, "same millisecond still counts");
  assert.equal(at(100, 250), true);
  assert.equal(at(300, 250), false, "re-graded after the late check");
});

test("reset progress: files from before the reset stop counting, files after it count again", () => {
  // Everything below is a finished question: tests, submissions, grades, late check, report.
  const finished = (at: number) =>
    state({
      tests: { ...state().tests, javaFiles: ["T.java"], reportConfig: true, newestMtimeMs: at },
      submissions: { count: 2, newestMtimeMs: at },
      results: {
        grades: grades(at + 10),
        scores: { mtimeMs: at + 20 },
        scoresBackup: { mtimeMs: at + 20 },
        report: { mtimeMs: at + 30 },
      },
    });
  const detected = (c: Ctx) => ["setup", "extract", "grade", "late", "report"].map((id) => describeStep(STEPS.findIndex((s) => s.id === id), c, {}).detected);

  const never = ctx({ zips: ZIPS }, finished(100));
  assert.deepEqual(detected(never), [true, true, true, true, true], "no reset: disk decides, as before");
  assert.equal(STEPS[currentStepIndex(never, {})].id, "materials");

  const reset = { ...never, since: 1000 };
  assert.deepEqual(detected(reset), [false, false, false, false, false], "reset after everything: nothing counts");
  assert.equal(STEPS[currentStepIndex(reset, {})].id, "materials");
  assert.equal(describeStep(STEPS.findIndex((s) => s.id === "setup"), reset, { setup: true }).done, true, "a hand tick still works");

  // New question's files arrive one by one, each newer than the reset.
  const withTests = { ...reset, state: { ...finished(100), tests: { ...finished(100).tests, newestMtimeMs: 2000 } } };
  assert.deepEqual(detected(withTests), [true, false, false, false, false]);
  const withSubs = { ...withTests, state: { ...withTests.state, submissions: { count: 2, newestMtimeMs: 2100 } } };
  assert.deepEqual(detected(withSubs), [true, true, false, false, false], "old grades are older than the new submissions");
  const regraded = {
    ...withSubs,
    state: { ...withSubs.state, results: { grades: grades(2200), scores: { mtimeMs: 2200 }, scoresBackup: { mtimeMs: 100 } } },
  };
  assert.deepEqual(detected(regraded), [true, true, true, false, false], "fresh grades, late check not run yet");

  // The reset only changes what counts as done; it must never lock the buttons.
  const grade = action("grade", "grade");
  assert.equal(actionProblem(step("grade"), grade, ctx({}, finished(100))), undefined);
  assert.equal(actionProblem(step("grade"), grade, { ...ctx({}, finished(100)), since: 1000 }), undefined);
});

test("grade and late name the files they write, so a locked one can be reported first", () => {
  const c = ctx({});
  const names = (stepId: string, actionId: string) => action(stepId, actionId).outputs!(c).map((f) => f.split(/[\\/]/).pop());
  assert.deepEqual(names("grade", "grade"), ["grades.csv", "mcvScore.csv"]);
  assert.deepEqual(names("late", "late"), ["mcvScore.csv", "mcvScore.csv.bak"]);
  assert.equal(action("extract", "extract").outputs, undefined);
});

test("every step renders without throwing on an empty folder", () => {
  const c = ctx({});
  STEPS.forEach((_, i) => {
    const v = describeStep(i, c, {});
    assert.ok(v.title && v.body.trim(), v.id);
  });
  const ids = STEPS.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length, "step ids are unique");
});
