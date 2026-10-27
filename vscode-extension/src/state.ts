// What the grading folder says about this week, read straight off disk. No
// vscode import (test/state.test.ts runs it on temp folders).
//
// Read-only by design: this module never writes, so refreshing the checklist
// can never disturb tests/, submissions/ or results/.

import * as fs from "fs";
import * as path from "path";

export interface FileInfo {
  mtimeMs: number;
}

export interface GradesSummary {
  rows: number;
  compiled: number;
  fullMarks: number;
  /** Counts of rows carrying each flag; one row can carry several. */
  flags: Record<FlagId, number>;
  averageScore: number;
  maxScore: number;
}

export interface GradingState {
  gradingDir: string;
  /** grade.py was found where gradingDir points. */
  gradingDirOk: boolean;
  tests: {
    javaFiles: string[];
    rubric: boolean;
    structure: boolean;
    manualReview: boolean;
    starter: boolean;
    reportConfig: boolean;
    /** report_config.json's assignment_title, when it has one. */
    assignmentTitle?: string;
    /** Newest change anywhere in tests/, deleting or adding a file included. */
    newestMtimeMs: number;
  };
  submissions: { count: number; newestMtimeMs: number };
  results: {
    grades?: FileInfo & { summary?: GradesSummary };
    scores?: FileInfo;
    scoresBackup?: FileInfo;
    report?: FileInfo;
  };
  setupRepo: { dir: string; ok: boolean; weeks: ArchivedWeek[] };
}

export interface ArchivedWeek {
  week: string;
  questions: string[];
}

// The flags step 6 counts. Each `marker` is a string grade.py (or, for the
// override, check_lateness.py) writes into the notes column. Those scripts own
// the wording; test/state.test.ts fails if a marker here stops appearing in the
// script that is supposed to write it, so a rewording can't drift silently.
export const FLAGS = [
  { id: "structure", label: "Structure error", marker: "STRUCTURE ERROR", source: "grade.py" },
  { id: "compile", label: "Compile error", marker: "COMPILE ERROR", source: "grade.py" },
  { id: "wrong", label: "Wrong submission likely", marker: "WRONG SUBMISSION LIKELY:", source: "grade.py" },
  { id: "capped", label: "Score capped", marker: "SCORE CAPPED AT", source: "grade.py" },
  { id: "timeout", label: "Timed out", marker: "timed out", source: "grade.py" },
  { id: "manual", label: "Manual review", marker: "MANUAL REVIEW", source: "grade.py" },
  { id: "unexpected", label: "Unexpected error (grader bug)", marker: "UNEXPECTED ERROR", source: "grade.py" },
  { id: "override", label: "TA override", marker: "TA OVERRIDE:", source: "check_lateness.py" },
] as const;

export type FlagId = (typeof FLAGS)[number]["id"] | "zeroTests";

function stat(p: string): FileInfo | undefined {
  try {
    const s = fs.statSync(p);
    return s.isFile() ? { mtimeMs: s.mtimeMs } : undefined;
  } catch {
    return undefined;
  }
}

function listDir(p: string): fs.Dirent[] {
  try {
    return fs.readdirSync(p, { withFileTypes: true });
  } catch {
    return [];
  }
}

/** The newest mtime under dir, dir itself included: a folder's own mtime moves
 *  when a file in it is added or removed, which a restore does to every file. */
function newestIn(dir: string): number {
  let newest = 0;
  try {
    newest = fs.statSync(dir).mtimeMs;
  } catch {
    return 0;
  }
  for (const d of listDir(dir)) {
    const p = path.join(dir, d.name);
    try {
      newest = Math.max(newest, d.isDirectory() ? newestIn(p) : fs.statSync(p).mtimeMs);
    } catch {
      // Gone between the listing and the stat: nothing to count.
    }
  }
  return newest;
}

/** RFC 4180: notes cells contain commas, quotes and newlines. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i++;
        } else {
          quoted = false;
        }
      } else {
        cell += c;
      }
    } else if (c === '"') {
      quoted = true;
    } else if (c === ",") {
      row.push(cell);
      cell = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += c;
    }
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

export function summarizeGrades(csvText: string): GradesSummary {
  const [header, ...body] = parseCsv(csvText.replace(/^﻿/, ""));
  const col = (name: string) => (header ?? []).indexOf(name);
  const iCompiled = col("compiled");
  const iTotal = col("tests_total");
  const iScore = col("score");
  const iMax = col("max_score");
  const iNotes = col("notes");
  const flags = Object.fromEntries([...FLAGS.map((f) => [f.id, 0]), ["zeroTests", 0]]) as Record<FlagId, number>;
  let compiled = 0;
  let fullMarks = 0;
  let scoreSum = 0;
  let maxScore = 0;
  const rows = body.filter((r) => r.length > 1);
  for (const r of rows) {
    const notes = r[iNotes] ?? "";
    const score = Number(r[iScore]);
    const max = Number(r[iMax]);
    if (r[iCompiled] === "yes") {
      compiled++;
      if ((r[iTotal] ?? "") === "0") flags.zeroTests++;
    }
    if (max > 0 && score === max) fullMarks++;
    if (Number.isFinite(score)) scoreSum += score;
    if (Number.isFinite(max)) maxScore = Math.max(maxScore, max);
    for (const f of FLAGS) if (notes.includes(f.marker)) flags[f.id]++;
  }
  return {
    rows: rows.length,
    compiled,
    fullMarks,
    flags,
    averageScore: rows.length ? scoreSum / rows.length : 0,
    maxScore,
  };
}

export function listArchivedWeeks(setupRepoDir: string): ArchivedWeek[] {
  return listDir(setupRepoDir)
    .filter((d) => d.isDirectory() && /^week\d{2}$/.test(d.name))
    .map((d) => ({
      week: d.name,
      questions: listDir(path.join(setupRepoDir, d.name))
        .filter((q) => q.isDirectory() && /^q\d+$/.test(q.name))
        .map((q) => q.name)
        .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1))),
    }))
    .filter((w) => w.questions.length > 0)
    .sort((a, b) => a.week.localeCompare(b.week));
}

export function readState(gradingDir: string, setupRepoDir: string): GradingState {
  const tests = path.join(gradingDir, "tests");
  const submissions = path.join(gradingDir, "submissions");
  const results = path.join(gradingDir, "results");

  let assignmentTitle: string | undefined;
  const configPath = path.join(tests, "report_config.json");
  const reportConfig = stat(configPath) !== undefined;
  if (reportConfig) {
    try {
      const cfg = JSON.parse(fs.readFileSync(configPath, "utf8"));
      if (typeof cfg.assignment_title === "string") assignmentTitle = cfg.assignment_title;
    } catch {
      // A half-written config is still "present"; build_report.py will say what is wrong.
    }
  }

  const subs = listDir(submissions).filter((d) => d.isFile() && d.name !== ".gitkeep");
  let newest = 0;
  for (const d of subs) newest = Math.max(newest, stat(path.join(submissions, d.name))?.mtimeMs ?? 0);

  const gradesPath = path.join(results, "grades.csv");
  const gradesInfo = stat(gradesPath);
  let grades: GradingState["results"]["grades"];
  if (gradesInfo) {
    let summary: GradesSummary | undefined;
    try {
      summary = summarizeGrades(fs.readFileSync(gradesPath, "utf8"));
    } catch {
      summary = undefined;
    }
    grades = { ...gradesInfo, summary };
  }

  return {
    gradingDir,
    gradingDirOk: stat(path.join(gradingDir, "grade.py")) !== undefined,
    tests: {
      javaFiles: listDir(tests)
        .filter((d) => d.isFile() && d.name.endsWith(".java"))
        .map((d) => d.name)
        .sort(),
      rubric: stat(path.join(tests, "rubric.json")) !== undefined,
      structure: stat(path.join(tests, "structure.json")) !== undefined,
      manualReview: stat(path.join(tests, "manual_review.json")) !== undefined,
      starter: listDir(path.join(tests, "starter")).length > 0,
      reportConfig,
      assignmentTitle,
      newestMtimeMs: newestIn(tests),
    },
    submissions: { count: subs.length, newestMtimeMs: newest },
    results: {
      grades,
      scores: stat(path.join(results, "mcvScore.csv")),
      scoresBackup: stat(path.join(results, "mcvScore.csv.bak")),
      report: stat(path.join(results, "report.html")),
    },
    setupRepo: {
      dir: setupRepoDir,
      ok: stat(path.join(setupRepoDir, "restore-week.sh")) !== undefined,
      weeks: listArchivedWeeks(setupRepoDir),
    },
  };
}
