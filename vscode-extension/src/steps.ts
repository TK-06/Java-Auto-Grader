// The weekly grading runbook, as data. Each step says what to do and why, which
// inputs it needs, which command(s) it runs, how to tell from disk that it is
// done, and what to ask an agent. No vscode import: test/steps.test.ts checks
// every command this file can build, argument by argument.
//
// Public repo: everything here is generic. Nothing week-specific, no student
// IDs, no answer-key content - those only ever come from the TA's own folders
// at runtime.
//
// What the extension never does: run anything that touches git. archive-week.sh
// commits and pushes, so it appears only as a command to copy.

import * as path from "path";
import { Command, displayCommand } from "./proc";
import { GradingState, FLAGS } from "./state";

export interface Paths {
  gradingDir: string;
  setupRepoDir: string;
  materialsDir: string;
  python: string;
  bash: string;
}

/** Every input the TA fills in, shared across steps (the zips picked in step 3
 *  are the same ones steps 4 and 7 need). Zip paths are newline-separated. */
export type Values = Record<string, string>;

export type FieldKind = "zips" | "week" | "question" | "deadline" | "text";

export interface Field {
  id: string;
  label: string;
  kind: FieldKind;
  placeholder?: string;
  hint?: string;
}

export interface Ctx {
  state: GradingState;
  paths: Paths;
  values: Values;
  /** When the TA last reset the checklist (ms since epoch). Files on disk that
   *  are not newer than this belong to the previous question, so they no longer
   *  count as a step being done. Unset = never reset. */
  since?: number;
  exists(p: string): boolean;
}

export interface Action {
  id: string;
  label: string;
  primary?: boolean;
  /** Fields shown with this action rather than in the shared Inputs card
   *  (step 6's three audits each have their own). */
  uses?: string[];
  /** Field ids that must be valid before the button enables. */
  needs: string[];
  /** Extra check beyond the fields; a message means "disabled, because ...". */
  blocked?(ctx: Ctx): string | undefined;
  /** A yes/no question asked before running, when the run is destructive. */
  confirm?(ctx: Ctx): string | undefined;
  /** Check `javac -version` is 25 first. */
  needsJdk25?: boolean;
  /** Files the script writes. Checked just before the run (see locks.ts), so a
   *  file open in Excel is reported in words instead of as a failed run. */
  outputs?(ctx: Ctx): string[];
  build(ctx: Ctx): Command;
}

export interface Fact {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "bad";
}

export interface Link {
  id: string;
  label: string;
  /** "folder" opens in the OS, "file" in an editor, "reveal" in Explorer, "browser" in the default browser. */
  kind: "folder" | "file" | "reveal" | "browser";
  target(ctx: Ctx): string | undefined;
}

export interface Prompt {
  label: string;
  text(ctx: Ctx): string;
}

export interface Step {
  id: string;
  title: string;
  summary: string;
  /** Trusted, static HTML written here - never built from runtime data. */
  body: string;
  fields: Field[];
  actions: Action[];
  copyCommands?: (ctx: Ctx) => { label: string; text: string }[];
  links?: Link[];
  prompts?: Prompt[];
  facts?(ctx: Ctx): Fact[];
  /** Detected from disk/inputs; undefined means "can't tell, tick it by hand". */
  done?(ctx: Ctx): boolean;
}

// ---------------------------------------------------------------- validation

export function zipsOf(values: Values): string[] {
  return (values.zips ?? "")
    .split("\n")
    .map((z) => z.trim())
    .filter(Boolean);
}

/**
 * The deadline exactly as check_lateness.py parses it: YYYY-MM-DDTHH:MM:SS,
 * nothing between the date and the T. A date-time picker gives
 * "2026-10-20T23:59" (no seconds); this adds them. Anything that isn't a real
 * calendar time comes back undefined, so the typo that once cost a re-run
 * (2026-09-06:T23:59:59) can't reach the script at all.
 */
export function normalizeDeadline(raw: string): string | undefined {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec((raw ?? "").trim());
  if (!m) return undefined;
  const [, y, mo, d, h, mi, s = "00"] = m;
  const date = new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi, +s));
  if (
    date.getUTCFullYear() !== +y ||
    date.getUTCMonth() !== +mo - 1 ||
    date.getUTCDate() !== +d ||
    +h > 23 ||
    +mi > 59 ||
    +s > 59
  ) {
    return undefined;
  }
  return `${y}-${mo}-${d}T${h}:${mi}:${s}`;
}

const NAME_LIST_RE = /^[A-Za-z_$][\w$]*(\s*,\s*[A-Za-z_$][\w$]*)*$/;
const NAME_RE = /^[A-Za-z_$][\w$]*$/;

/** Why a field's value is not usable yet, or undefined when it is. */
export function fieldError(field: Field, ctx: Ctx): string | undefined {
  const v = (ctx.values[field.id] ?? "").trim();
  switch (field.kind) {
    case "week":
      return /^week\d{2}$/.test(v) ? undefined : "Week must look like week05.";
    case "question":
      return /^[1-9]\d?$/.test(v) ? undefined : "Question must be a number, e.g. 2.";
    case "deadline":
      return normalizeDeadline(v) ? undefined : "Pick the assignment's deadline (Thailand time).";
    case "zips": {
      const zips = zipsOf(ctx.values);
      if (!zips.length) return "Pick the MyCourseVille export zip(s) in step 3.";
      const bad = zips.find((z) => !/\.zip$/i.test(z) || !ctx.exists(z));
      return bad ? `Not found or not a .zip: ${bad}` : undefined;
    }
    case "text":
      return undefined;
  }
}

/** Optional comma-separated identifiers: empty is fine, garbage is not. */
function nameListError(v: string | undefined, what: string): string | undefined {
  const s = (v ?? "").trim();
  return !s || NAME_LIST_RE.test(s) ? undefined : `${what} must be comma-separated Java names.`;
}

const csv = (v: string | undefined) =>
  (v ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .join(",");

/** Bash on Windows wants forward slashes in the paths it is handed. */
const posix = (p: string) => p.replace(/\\/g, "/");

const py = (ctx: Ctx, script: string, ...args: string[]): Command => ({
  exe: ctx.paths.python,
  args: [script, ...args],
  cwd: ctx.paths.gradingDir,
});

function isArchived(ctx: Ctx): boolean {
  const w = ctx.state.setupRepo.weeks.find((x) => x.week === ctx.values.week);
  return !!w && w.questions.includes(`q${ctx.values.question}`);
}

const ago = (ms: number) => {
  const mins = Math.round((Date.now() - ms) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 48 ? `${hrs} h ago` : `${Math.round(hrs / 24)} days ago`;
};

const rel = (ctx: Ctx, ...p: string[]) => path.join(ctx.paths.gradingDir, ...p);

/** True when a file counts for the current question: always before any reset,
 *  and after one only if it changed since. */
const fresh = (ctx: Ctx, mtimeMs: number) => ctx.since === undefined || mtimeMs > ctx.since;

/** results/grades.csv when it holds at least one graded row. grade.py creates
 *  an empty one before it starts (to check it can write there), so a run that
 *  was cancelled on a fresh week leaves a 0-row file that is not a result. */
const graded = (ctx: Ctx) => {
  const g = ctx.state.results.grades;
  return g?.summary && g.summary.rows > 0 ? g : undefined;
};

/** check_lateness.py copies mcvScore.csv to .bak (a fresh mtime) every time it
 *  applies caps, and grade.py never writes the .bak - so a .bak at least as new
 *  as grades.csv means the late check ran after the latest grading run. */
const lateApplied = (ctx: Ctx) => {
  const g = graded(ctx);
  const bak = ctx.state.results.scoresBackup;
  return !!g && !!bak && bak.mtimeMs >= g.mtimeMs;
};

// --------------------------------------------------------------------- steps

const SCRATCH_RULE =
  "Put any scratch, backup or extracted copy of student data in your session scratchpad, " +
  "never in a new folder inside grading/ – only submissions/ and results/ are gitignored.";

export const STEPS: Step[] = [
  {
    id: "materials",
    title: "Get the week's materials",
    summary: "Assignment, marking guide, Solution/ and toStudent/ from the professor.",
    body: `
      <p>The professor's folder for the week holds the assignment PDF, the
      <code>Q&lt;N&gt;_HowToMark.docx</code> marking guide, a <code>Solution/</code> reference
      implementation (the answer key) and a <code>toStudent/</code> starter.</p>
      <p><strong>Check the points before anything else.</strong> Add up the JUnit points in the
      marking guide and compare them with the total the assignment states. If the JUnit points
      come to less, the difference is a <em>hand-graded</em> part – step 10 – that the automated
      run will never cover.</p>
      <p>A <code>.docx</code> is binary; the command below prints its text in Git Bash.</p>`,
    fields: [
      { id: "week", label: "Week", kind: "week", placeholder: "week10" },
      { id: "question", label: "Question", kind: "question", placeholder: "2" },
    ],
    actions: [],
    copyCommands: (ctx) => [
      {
        label: "Print the marking guide's text (Git Bash, in the question's folder)",
        text:
          `unzip -p "Q${ctx.values.question || "<N>"}_HowToMark.docx" word/document.xml | ` +
          `sed -e 's/<[^>]*>/ /g' -e 's/&amp;/\\&/g' -e 's/&lt;/</g' -e 's/&gt;/>/g'`,
      },
    ],
    links: [
      {
        id: "materials",
        label: "Open the materials folder",
        kind: "folder",
        target: (ctx) => ctx.paths.materialsDir || undefined,
      },
    ],
    prompts: [
      {
        label: "Read the materials",
        text: (ctx) =>
          `Read this week's grading materials for ${ctx.values.week || "<week>"} Q${ctx.values.question || "<N>"}` +
          `${ctx.paths.materialsDir ? ` under ${ctx.paths.materialsDir}` : ""}: the assignment, the ` +
          `Q<N>_HowToMark.docx marking guide (extract its text with unzip -p ... word/document.xml | sed), ` +
          `Solution/ and toStudent/. Report: (1) the per-test JUnit point split, verbatim; (2) the ` +
          `assignment's stated total; (3) whether a hand-graded component exists – the stated total ` +
          `minus the JUnit sum; (4) which classes and methods the student writes, from diffing Solution/ ` +
          `against toStudent/. Read only – do not change any file.`,
      },
    ],
  },
  {
    id: "setup",
    title: "Load the week's test setup",
    summary: "Restore the archived official tests, rubric and structure into tests/.",
    body: `
      <p>If this week and question were graded in an earlier term, its setup is archived in the
      private setup repo, and restoring it is the whole step: the official tests plus whichever
      of <code>rubric.json</code>, <code>structure.json</code>, <code>manual_review.json</code> and
      <code>starter/</code> were saved.</p>
      <p><strong>Not archived yet?</strong> Then it has to be authored from the materials, following
      <em>Authoring a week's setup from scratch</em> in the project's <code>CLAUDE.md</code>, and
      validated against two synthetic submissions (the solution must score full marks, the
      untouched starter a clean 0) before any real one. Archiving it afterwards commits and pushes
      to the private repo, so that command is only shown here, never run.</p>`,
    fields: [
      { id: "week", label: "Week", kind: "week", placeholder: "week10" },
      { id: "question", label: "Question", kind: "question", placeholder: "2" },
    ],
    actions: [
      {
        id: "restore",
        label: "Restore into tests/",
        primary: true,
        needs: ["week", "question"],
        blocked: (ctx) => {
          if (!ctx.state.setupRepo.ok) return `restore-week.sh not found in ${ctx.paths.setupRepoDir} – set dsaGrading.setupRepoDir.`;
          if (!isArchived(ctx)) return `${ctx.values.week}/q${ctx.values.question} is not archived – author it from scratch (below).`;
          return undefined;
        },
        confirm: (ctx) =>
          ctx.state.tests.javaFiles.length
            ? `This replaces everything in tests/ (currently ${ctx.state.tests.javaFiles.join(", ")}) ` +
              `with the archived ${ctx.values.week}/q${ctx.values.question}. Continue?`
            : undefined,
        build: (ctx) => ({
          exe: ctx.paths.bash,
          args: [
            posix(path.join(ctx.paths.setupRepoDir, "restore-week.sh")),
            ctx.values.week.trim(),
            `q${ctx.values.question.trim()}`,
            posix(ctx.paths.gradingDir),
          ],
          cwd: ctx.paths.setupRepoDir,
        }),
      },
    ],
    copyCommands: (ctx) => [
      {
        label: "After authoring and validating a new setup – archive it (commits and pushes; run it yourself)",
        text: `cd "${posix(ctx.paths.setupRepoDir)}" && ./archive-week.sh ${ctx.values.week || "weekNN"} q${ctx.values.question || "N"}`,
      },
    ],
    prompts: [
      {
        label: "Author this week's setup from scratch",
        text: (ctx) =>
          `Author the grading setup for ${ctx.values.week || "<week>"} Q${ctx.values.question || "<N>"} ` +
          `in ${ctx.paths.gradingDir}/tests, following "Authoring a week's setup from scratch" in the ` +
          `project CLAUDE.md, step by step: read the marking guide and assignment, diff Solution/ against ` +
          `toStudent/, check the starter's packages against the official test, write rubric.json and ` +
          `structure.json (with student_classes), decide manual_review.json and starter/, then validate ` +
          `against the two synthetic submissions in your scratchpad. Stop and report before ` +
          `archive-week.sh – I run that myself. ${SCRATCH_RULE}`,
      },
    ],
    facts: (ctx) => {
      const t = ctx.state.tests;
      const facts: Fact[] = [
        t.javaFiles.length
          ? { label: "tests/ now holds", value: t.javaFiles.join(", "), tone: "ok" }
          : { label: "tests/ now holds", value: "no official test yet", tone: "warn" },
        {
          label: "Config",
          value:
            [t.rubric && "rubric", t.structure && "structure", t.manualReview && "manual review", t.starter && "starter", t.reportConfig && "report config"]
              .filter(Boolean)
              .join(", ") || "none",
        },
      ];
      if (!ctx.state.setupRepo.ok) {
        facts.push({ label: "Setup repo", value: `not found at ${ctx.paths.setupRepoDir}`, tone: "bad" });
      } else if (ctx.values.week && ctx.values.question) {
        facts.push(
          isArchived(ctx)
            ? { label: "Archive", value: `${ctx.values.week}/q${ctx.values.question} is archived`, tone: "ok" }
            : { label: "Archive", value: `${ctx.values.week}/q${ctx.values.question} is not archived yet`, tone: "warn" },
        );
      }
      return facts;
    },
    done: (ctx) => ctx.state.tests.javaFiles.length > 0 && fresh(ctx, ctx.state.tests.newestMtimeMs),
  },
  {
    id: "zips",
    title: "Get the student submissions",
    summary: "Download MyCourseVille's bulk attachment export (one or more zip parts).",
    body: `
      <p>Download the assignment's <strong>bulk attachment export</strong> from MyCourseVille. A large
      class comes as several parts (<code>attachments.Attachment_File.1-...zip</code>,
      <code>.2-...</code>, ...), split by size with no overlap – pick all of them.</p>
      <p><strong>Keep the zips until step 7.</strong> Extracting renames each submission to
      <code>&lt;studentID&gt;.&lt;ext&gt;</code> and drops the timestamp in the original file name;
      the late-penalty check reads the real submission times back out of these zips.</p>`,
    fields: [{ id: "zips", label: "Export zip(s)", kind: "zips" }],
    actions: [],
    facts: (ctx) => {
      const zips = zipsOf(ctx.values);
      return [
        zips.length
          ? { label: "Picked", value: zips.map((z) => path.basename(z)).join(", "), tone: "ok" }
          : { label: "Picked", value: "nothing yet", tone: "warn" },
      ];
    },
    done: (ctx) => zipsOf(ctx.values).length > 0,
  },
  {
    id: "extract",
    title: "Extract into submissions/",
    summary: "One clean <studentID>.<ext> per student, latest resubmission only.",
    body: `
      <p>Never hand the export to the grader directly. A student's folder often holds <em>both</em>
      questions' files, resubmissions, or something unrelated attached to the wrong slot.
      <code>extract_submissions.py</code> keeps this question's latest file per student (by the
      timestamp in its name), skips Windows' path-length limit, and writes
      <code>submissions/&lt;studentID&gt;.&lt;ext&gt;</code>.</p>
      <p><strong>It empties submissions/ first.</strong> Try the dry run to see what it would do.</p>
      <p>Then read its output: every <em>Needs a look</em> line is something to check by hand, and
      the <code>"not_submitted"</code> / <code>"no_valid_q&lt;N&gt;"</code> lines at the end go into
      <code>tests/report_config.json</code> for step 9.</p>`,
    fields: [
      { id: "zips", label: "Export zip(s)", kind: "zips" },
      { id: "question", label: "Question", kind: "question", placeholder: "2" },
    ],
    actions: [
      {
        id: "dry",
        label: "Dry run",
        needs: ["zips", "question"],
        build: (ctx) => py(ctx, "extract_submissions.py", ...zipsOf(ctx.values), "--question", ctx.values.question.trim(), "--dry-run"),
      },
      {
        id: "extract",
        label: "Extract",
        primary: true,
        needs: ["zips", "question"],
        confirm: (ctx) =>
          ctx.state.submissions.count
            ? `This empties submissions/ (${ctx.state.submissions.count} file(s) now) and extracts Q${ctx.values.question} again. Continue?`
            : undefined,
        build: (ctx) => py(ctx, "extract_submissions.py", ...zipsOf(ctx.values), "--question", ctx.values.question.trim()),
      },
    ],
    prompts: [
      {
        label: "Check the extraction",
        text: (ctx) =>
          `I just ran extract_submissions.py in ${ctx.paths.gradingDir} for Q${ctx.values.question || "<N>"}; ` +
          `its output is in the "DSA Grading" terminal (paste it below). For every "Needs a look" and ` +
          `"Unusually small" line, inspect that student's file and tell me whether it is a real ` +
          `submission for this question. Do not change submissions/. ${SCRATCH_RULE}`,
      },
    ],
    facts: (ctx) => [
      ctx.state.submissions.count
        ? { label: "submissions/", value: `${ctx.state.submissions.count} file(s)`, tone: "ok" }
        : { label: "submissions/", value: "empty", tone: "warn" },
    ],
    done: (ctx) => ctx.state.submissions.count > 0 && fresh(ctx, ctx.state.submissions.newestMtimeMs),
  },
  {
    id: "grade",
    title: "Run the grader",
    summary: "Compile and test every submission; writes results/grades.csv and mcvScore.csv.",
    body: `
      <p><code>grade.py</code> compiles each submission together with the official tests (the
      official copy always wins over one the student bundled), runs JUnit, applies the grading
      policy's caps, and writes <code>results/grades.csv</code> (the detailed record) and
      <code>results/mcvScore.csv</code> (bare IDs and scores for the gradebook).</p>
      <p>It needs <strong>JDK 25</strong>; that is checked before it starts.</p>
      <p><strong>A re-run regenerates grades.csv from scratch</strong>, dropping any hand-applied
      <code>TA OVERRIDE:</code> edits. Re-apply them afterwards, before step 7.</p>`,
    fields: [],
    actions: [
      {
        id: "grade",
        label: "Run grade.py",
        primary: true,
        needs: [],
        needsJdk25: true,
        outputs: (ctx) => [rel(ctx, "results", "grades.csv"), rel(ctx, "results", "mcvScore.csv")],
        blocked: (ctx) => {
          if (!ctx.state.tests.javaFiles.length) return "No official test in tests/ – do step 2 first.";
          if (!ctx.state.submissions.count) return "submissions/ is empty – do step 4 first.";
          return undefined;
        },
        confirm: (ctx) => {
          const overrides = ctx.state.results.grades?.summary?.flags.override ?? 0;
          return overrides
            ? `results/grades.csv has ${overrides} TA OVERRIDE row(s). Re-grading regenerates it and drops them. Continue?`
            : undefined;
        },
        build: (ctx) => py(ctx, "grade.py"),
      },
    ],
    facts: (ctx) => {
      const g = ctx.state.results.grades;
      const facts: Fact[] = [
        { label: "Official tests", value: ctx.state.tests.javaFiles.join(", ") || "none", tone: ctx.state.tests.javaFiles.length ? "ok" : "warn" },
        { label: "Submissions", value: String(ctx.state.submissions.count), tone: ctx.state.submissions.count ? "ok" : "warn" },
      ];
      if (g?.summary && g.summary.rows === 0) {
        facts.push({ label: "Last run", value: "grades.csv is empty – a run was cancelled before it finished", tone: "warn" });
      } else if (g?.summary) {
        const stale = g.mtimeMs < ctx.state.submissions.newestMtimeMs;
        facts.push({
          label: "Last run",
          value:
            `${ago(g.mtimeMs)}: ${g.summary.compiled}/${g.summary.rows} compiled, ` +
            `average ${g.summary.averageScore.toFixed(2)}/${g.summary.maxScore}` +
            (stale ? " – older than submissions/, re-run" : ""),
          tone: stale ? "warn" : "ok",
        });
      }
      return facts;
    },
    done: (ctx) => {
      const g = graded(ctx);
      return !!g && ctx.state.submissions.count > 0 && g.mtimeMs >= ctx.state.submissions.newestMtimeMs && fresh(ctx, g.mtimeMs);
    },
  },
  {
    id: "validate",
    title: "Validate the results",
    summary: "Look at every non-clean row – don't just skim for zeros.",
    body: `
      <p>Categorise every row that isn't a clean pass. <code>UNEXPECTED ERROR</code> is a grader bug,
      not a student one. A <code>MANUAL REVIEW</code> flag costs nothing by itself; it is for you to
      decide.</p>
      <p><strong>Investigate, don't just read the error.</strong> When something looks surprising,
      reproduce that student's compile and test run yourself before trusting the row. Source that
      belongs to a different question scores 0, but confirm it against the other question's material
      first.</p>
      <p>To override a result for a policy reason, edit the row in <code>grades.csv</code> so the
      technical columns stay true and <code>score</code>/<code>score_cap</code> carry the decision,
      with a <code>TA OVERRIDE:</code> note. Keep the student-facing wording in
      <code>report_config.json</code>'s <code>ta_notes</code> so a re-grade can't lose it.</p>
      <p>The audits below are read-only evidence for you, never a score change.</p>`,
    fields: [
      { id: "dupeWhole", label: "Classes the student wrote in full", kind: "text", placeholder: "BankQueue" },
      { id: "dupeClasses", label: "Classes compared by one method", kind: "text", placeholder: "BST" },
      { id: "dupeMethod", label: "That method", kind: "text", placeholder: "height" },
      { id: "allow", label: "Methods the student was told to write", kind: "text", placeholder: "height,makeBalancedTree", hint: "Leave empty to audit every given method." },
      { id: "givenClasses", label: "Classes (empty = every class in tests/starter/)", kind: "text", placeholder: "BST" },
      { id: "entry", label: "The method the rule applies to", kind: "text", placeholder: "removeRange" },
      { id: "forbiddenClasses", label: "Classes to audit", kind: "text", placeholder: "StackArray,StackLinkedList" },
      { id: "forbidden", label: "Forbidden method names", kind: "text", placeholder: "pop,push" },
    ],
    actions: [
      {
        id: "dupe",
        uses: ["dupeWhole", "dupeClasses", "dupeMethod"],
        label: "Run dupe_check.py",
        needs: [],
        blocked: (ctx) => {
          const v = ctx.values;
          if (!csv(v.dupeWhole) && !csv(v.dupeClasses)) return "Fill in classes to compare.";
          if (csv(v.dupeClasses) && !NAME_RE.test((v.dupeMethod ?? "").trim())) return "Name the method to compare in those classes.";
          return nameListError(v.dupeWhole, "Classes") ?? nameListError(v.dupeClasses, "Classes");
        },
        build: (ctx) => {
          const v = ctx.values;
          const args: string[] = [];
          if (csv(v.dupeWhole)) args.push("--whole-file", csv(v.dupeWhole));
          if (csv(v.dupeClasses)) args.push("--classes", csv(v.dupeClasses), "--method", v.dupeMethod.trim());
          return py(ctx, "dupe_check.py", ...args);
        },
      },
      {
        id: "given",
        uses: ["allow", "givenClasses"],
        label: "Run audit_given_code.py",
        needs: [],
        blocked: (ctx) => {
          if (!ctx.state.tests.starter) return "tests/starter/ is empty – this audit compares against the unedited starter.";
          return nameListError(ctx.values.allow, "Allowed methods") ?? nameListError(ctx.values.givenClasses, "Classes");
        },
        build: (ctx) => {
          const v = ctx.values;
          const args = ["--allow", csv(v.allow)];
          if (csv(v.givenClasses)) args.push("--classes", csv(v.givenClasses));
          return py(ctx, "audit_given_code.py", ...args, "--diff");
        },
      },
      {
        id: "forbidden",
        uses: ["entry", "forbiddenClasses", "forbidden"],
        label: "Run audit_forbidden.py",
        needs: [],
        blocked: (ctx) => {
          const v = ctx.values;
          if (!NAME_RE.test((v.entry ?? "").trim())) return "Name the method the rule applies to.";
          if (!csv(v.forbiddenClasses) || !csv(v.forbidden)) return "Fill in the classes and the forbidden methods.";
          return nameListError(v.forbiddenClasses, "Classes") ?? nameListError(v.forbidden, "Forbidden methods");
        },
        build: (ctx) => {
          const v = ctx.values;
          return py(ctx, "audit_forbidden.py", "--entry", v.entry.trim(), "--classes", csv(v.forbiddenClasses), "--forbidden", csv(v.forbidden));
        },
      },
    ],
    links: [{ id: "grades", label: "Open results/grades.csv", kind: "file", target: (ctx) => rel(ctx, "results", "grades.csv") }],
    prompts: [
      {
        label: "Validate grades.csv",
        text: (ctx) =>
          `Validate ${ctx.paths.gradingDir}/results/grades.csv following step 6 of the project CLAUDE.md. ` +
          `Categorise every non-clean row (STRUCTURE ERROR, COMPILE ERROR, score caps, 0 tests found, ` +
          `timeouts, UNEXPECTED ERROR, MANUAL REVIEW). For anything surprising, reproduce that student's ` +
          `compile and test run yourself before trusting the row, and check wrong-question source against ` +
          `the other question's real material. Report findings with evidence; do not edit results/ ` +
          `without asking me first. ${SCRATCH_RULE}`,
      },
    ],
    facts: (ctx) => {
      const s = graded(ctx)?.summary;
      if (!s) return [{ label: "grades.csv", value: "no graded rows yet – run step 5", tone: "warn" }];
      const facts: Fact[] = [
        { label: "Rows", value: `${s.rows} graded, ${s.compiled} compiled, ${s.fullMarks} full marks`, tone: "ok" },
      ];
      for (const f of FLAGS) {
        const n = s.flags[f.id];
        if (n) facts.push({ label: f.label, value: String(n), tone: f.id === "unexpected" ? "bad" : f.id === "override" ? "ok" : "warn" });
      }
      if (s.flags.zeroTests) facts.push({ label: "Compiled, 0 tests found", value: String(s.flags.zeroTests), tone: "bad" });
      return facts;
    },
  },
  {
    id: "late",
    title: "Apply late penalties",
    summary: "Cap late students in mcvScore.csv from the real submission times.",
    body: `
      <p><code>check_lateness.py</code> reads each student's real submission time from the
      <strong>original export zips</strong> and, with the deadline, caps late students in
      <code>results/mcvScore.csv</code> – the file you upload. <code>grades.csv</code> is only read,
      never changed, and the previous <code>mcvScore.csv</code> is kept as <code>.bak</code>.</p>
      <p>The penalty is a <strong>ceiling</strong>, not a deduction: <em>n</em> days late (a partial day
      counts as a full one) caps the score at (100 – 10<em>n</em>)% of the total, compared with any
      cap the grader already applied – the stricter one wins. A row with a <code>TA OVERRIDE:</code>
      note is reported and left alone.</p>
      <p><strong>Apply grades.csv overrides first</strong>: this rebuilds mcvScore.csv from grades.csv
      every time, so it is always safe to run again. The deadline is in Thailand time.</p>`,
    fields: [
      { id: "zips", label: "Export zip(s)", kind: "zips" },
      { id: "deadline", label: "Deadline (Thailand time)", kind: "deadline" },
      { id: "question", label: "Question", kind: "question", placeholder: "2" },
    ],
    actions: [
      {
        id: "late",
        label: "Run check_lateness.py",
        primary: true,
        needs: ["zips", "deadline", "question"],
        outputs: (ctx) => [rel(ctx, "results", "mcvScore.csv"), rel(ctx, "results", "mcvScore.csv.bak")],
        blocked: (ctx) => (graded(ctx) ? undefined : "results/grades.csv has no graded rows yet – do step 5 first."),
        build: (ctx) =>
          py(
            ctx,
            "check_lateness.py",
            ...zipsOf(ctx.values),
            "--deadline",
            normalizeDeadline(ctx.values.deadline) as string,
            "--question",
            ctx.values.question.trim(),
          ),
      },
    ],
    facts: (ctx) => {
      const r = ctx.state.results;
      const g = graded(ctx);
      if (!g) return [{ label: "grades.csv", value: "no graded rows yet", tone: "warn" }];
      const applied = lateApplied(ctx);
      return [
        applied
          ? { label: "mcvScore.csv", value: `late caps applied ${ago(r.scoresBackup!.mtimeMs)}`, tone: "ok" }
          : { label: "mcvScore.csv", value: "as the grader wrote it – no late check since the last run", tone: "warn" },
      ];
    },
    done: (ctx) => lateApplied(ctx) && fresh(ctx, ctx.state.results.scoresBackup?.mtimeMs ?? 0),
  },
  {
    id: "upload",
    title: "Upload scores to MyCourseVille",
    summary: "Import results/mcvScore.csv into the gradebook by hand.",
    body: `
      <p>Upload <code>results/mcvScore.csv</code> to the MyCourseVille gradebook yourself. If step 7
      ran, the late penalties are already in it.</p>
      <p><strong>Uploaded before the late check?</strong> Then the rewritten file has to be uploaded
      again to correct anyone who turned out to be late.</p>
      <p>Tick this step once the upload is done.</p>`,
    fields: [],
    actions: [],
    links: [{ id: "scores", label: "Show mcvScore.csv in Explorer", kind: "reveal", target: (ctx) => rel(ctx, "results", "mcvScore.csv") }],
  },
  {
    id: "report",
    title: "Build the class report",
    summary: "One searchable page for the whole roster, shared as an Artifact link.",
    body: `
      <p><code>build_report.py</code> turns <code>grades.csv</code> into
      <code>results/report.html</code>: every student, including the ones who submitted nothing, with
      an explanation written for the student.</p>
      <p>It needs this week's <code>tests/report_config.json</code>: the assignment title and points,
      the <code>not_submitted</code> / <code>no_valid_q&lt;N&gt;</code> lists from step 4, any
      <code>late_penalties</code> and <code>ta_notes</code>, and a plain-language
      <code>test_order</code> written from reading the real test assertions.</p>
      <p>Then publish <code>results/report.html</code> as an Artifact and share the link with the
      class.</p>`,
    fields: [],
    actions: [
      {
        id: "report",
        label: "Run build_report.py",
        primary: true,
        needs: [],
        blocked: (ctx) => {
          if (!graded(ctx)) return "results/grades.csv has no graded rows yet – do step 5 first.";
          if (!ctx.state.tests.reportConfig) return "tests/report_config.json is missing – write it first (prompt below).";
          return undefined;
        },
        build: (ctx) => py(ctx, "build_report.py"),
      },
    ],
    links: [
      { id: "config", label: "Open tests/report_config.json", kind: "file", target: (ctx) => rel(ctx, "tests", "report_config.json") },
      { id: "report", label: "Open the report in the browser", kind: "browser", target: (ctx) => rel(ctx, "results", "report.html") },
    ],
    prompts: [
      {
        label: "Write report_config.json",
        text: (ctx) =>
          `Write ${ctx.paths.gradingDir}/tests/report_config.json for ${ctx.values.week || "this week"} ` +
          `Q${ctx.values.question || "<N>"}, following "The report artifact" in the project CLAUDE.md: ` +
          `assignment_eyebrow, assignment_title, automated_points, total_points/manual_component_desc if ` +
          `there is a hand-graded part, not_submitted and no_valid_q<N> from the extraction output, ` +
          `late_penalties, ta_notes, and test_order written from reading the real test assertions in ` +
          `tests/, never guessed from method names.`,
      },
      {
        label: "Publish the report",
        text: (ctx) =>
          `Publish ${ctx.paths.gradingDir}/results/report.html as an Artifact (it is the class's grading ` +
          `report, keyed by student ID) and give me the link to share with the class.`,
      },
    ],
    facts: (ctx) => {
      const r = ctx.state.results;
      const facts: Fact[] = [
        ctx.state.tests.reportConfig
          ? { label: "report_config.json", value: ctx.state.tests.assignmentTitle ?? "present", tone: "ok" }
          : { label: "report_config.json", value: "missing", tone: "warn" },
      ];
      if (r.report) {
        const stale = !!r.grades && r.report.mtimeMs < r.grades.mtimeMs;
        facts.push({ label: "report.html", value: `built ${ago(r.report.mtimeMs)}${stale ? " – older than grades.csv, rebuild" : ""}`, tone: stale ? "warn" : "ok" });
      }
      return facts;
    },
    done: (ctx) => {
      const r = ctx.state.results;
      const g = graded(ctx);
      return !!r.report && !!g && r.report.mtimeMs >= g.mtimeMs && fresh(ctx, r.report.mtimeMs);
    },
  },
  {
    id: "manual",
    title: "Hand-grade the non-JUnit part",
    summary: "Only after upload, only for the students assigned to you.",
    body: `
      <p>Only when step 1 found a hand-graded component, only after scores are uploaded, and only for
      the student IDs the professor assigned to you.</p>
      <p>Review each one against the marking guide's non-JUnit criteria, with <code>Solution/</code> as
      the answer key. Grade by the rubric's literal text by default. When a student reaches the same
      intent another legitimate way, flag every such case with the code as evidence and apply one
      ruling to all of them at once.</p>
      <p>Keep a suspected-copying question separate from a scoring question – they can be answered
      independently.</p>`,
    fields: [{ id: "assigned", label: "Student IDs assigned to you", kind: "text", placeholder: "comma-separated" }],
    actions: [],
    prompts: [
      {
        label: "Review the assigned students",
        text: (ctx) =>
          `For the student IDs ${ctx.values.assigned?.trim() || "<IDs>"}, review their submissions in ` +
          `${ctx.paths.gradingDir}/submissions against the marking guide's hand-graded (non-JUnit) ` +
          `criteria, using Solution/ as the answer key. Grade by the rubric's literal text; flag every ` +
          `case that reaches the same intent a different legitimate way, with the code as evidence, and ` +
          `ask me for one ruling across all of them. Report suspected copying separately. ${SCRATCH_RULE}`,
      },
    ],
  },
];

// ------------------------------------------------------- serialisable view

export interface ActionView {
  id: string;
  label: string;
  primary: boolean;
  uses: string[];
  /** Why the button is disabled; undefined = enabled. */
  disabled?: string;
  command?: string;
}

export interface StepView {
  index: number;
  id: string;
  title: string;
  summary: string;
  body: string;
  fields: (Field & { value: string; error?: string })[];
  actions: ActionView[];
  copyCommands: { label: string; text: string }[];
  links: { id: string; label: string; available: boolean }[];
  prompts: { label: string; text: string }[];
  facts: Fact[];
  /** true/false when detected, undefined when only a manual tick can say. */
  detected?: boolean;
  ticked: boolean;
  done: boolean;
}

/** First problem that keeps an action from running, or undefined. */
export function actionProblem(step: Step, action: Action, ctx: Ctx): string | undefined {
  for (const id of action.needs) {
    const field = step.fields.find((f) => f.id === id);
    if (field) {
      const err = fieldError(field, ctx);
      if (err) return err;
    }
  }
  return action.blocked?.(ctx);
}

export function describeStep(index: number, ctx: Ctx, ticks: Record<string, boolean>): StepView {
  const step = STEPS[index];
  const detected = step.done?.(ctx);
  const ticked = !!ticks[step.id];
  return {
    index,
    id: step.id,
    title: step.title,
    summary: step.summary,
    body: step.body,
    fields: step.fields.map((f) => ({
      ...f,
      value: ctx.values[f.id] ?? "",
      error: (ctx.values[f.id] ?? "") === "" && f.kind !== "zips" ? undefined : fieldError(f, ctx),
    })),
    actions: step.actions.map((a) => {
      const disabled = actionProblem(step, a, ctx);
      let command: string | undefined;
      if (!disabled) {
        command = displayCommand(a.build(ctx));
      }
      return { id: a.id, label: a.label, primary: !!a.primary, uses: a.uses ?? [], disabled, command };
    }),
    copyCommands: step.copyCommands?.(ctx) ?? [],
    links: (step.links ?? []).map((l) => {
      const t = l.target(ctx);
      return { id: l.id, label: l.label, available: !!t && (l.kind === "folder" || ctx.exists(t)) };
    }),
    prompts: (step.prompts ?? []).map((p) => ({ label: p.label, text: p.text(ctx) })),
    facts: step.facts?.(ctx) ?? [],
    detected,
    ticked,
    done: ticked || detected === true,
  };
}

/** The first step that is neither detected nor ticked as done. */
export function currentStepIndex(ctx: Ctx, ticks: Record<string, boolean>): number {
  const i = STEPS.findIndex((s) => !(ticks[s.id] || s.done?.(ctx) === true));
  return i === -1 ? STEPS.length - 1 : i;
}
