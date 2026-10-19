// Turns the grader's console output into progress the UI can draw. No vscode
// import, so it is unit-tested with plain node (test/progress.test.ts).
//
// grade.py prints two kinds of counter line, both "[i/total] ...":
//   "  [3/40] unpacking 10000003.jar ..."            while it extracts archives
//   "[3/40] 10000003: compiled, 9/11 tests passed (score 9/11)"   per student graded
// and one summary line at the very end:
//   "  compiled: 38/40   average score: 8.10   0-tests-found: 0   timeouts: 1"
// If grade.py's wording ever changes, the run still works - the bar just stops
// moving - and test/progress.test.ts is where to fix the patterns.

export interface Progress {
  done: number;
  total: number;
  /** 0..100 */
  percent: number;
  /** "unpacking" while archives are extracted, "grading" per student. */
  phase: string;
  /** The rest of the line, for the progress message. */
  message: string;
}

const COUNTER_RE = /^\s*\[(\d+)\/(\d+)\]\s+(.*)$/;
const SUMMARY_RE = /compiled:\s*(\d+)\/(\d+)\s+average score:\s*([\d.]+)\s+0-tests-found:\s*(\d+)\s+timeouts:\s*(\d+)/;

export function parseProgress(line: string): Progress | undefined {
  const m = COUNTER_RE.exec(line);
  if (!m) return undefined;
  const done = Number(m[1]);
  const total = Number(m[2]);
  if (!total || done > total) return undefined;
  const rest = m[3].trim();
  const phase = rest.startsWith("unpacking ") ? "unpacking" : "grading";
  return { done, total, percent: Math.round((done / total) * 100), phase, message: rest };
}

export interface GradeSummary {
  compiled: number;
  rows: number;
  average: number;
  zeroTests: number;
  timeouts: number;
}

export function parseGradeSummary(line: string): GradeSummary | undefined {
  const m = SUMMARY_RE.exec(line);
  if (!m) return undefined;
  return {
    compiled: Number(m[1]),
    rows: Number(m[2]),
    average: Number(m[3]),
    zeroTests: Number(m[4]),
    timeouts: Number(m[5]),
  };
}

/** Splits a byte stream into whole lines, holding back a trailing partial one. */
export class LineSplitter {
  private buffer = "";

  push(chunk: string): string[] {
    this.buffer += chunk;
    const parts = this.buffer.split(/\r?\n/);
    this.buffer = parts.pop() ?? "";
    return parts;
  }

  /** Whatever is left once the stream has ended. */
  flush(): string[] {
    const rest = this.buffer;
    this.buffer = "";
    return rest ? [rest] : [];
  }
}
