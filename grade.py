#!/usr/bin/env python3
"""Auto-Grader for Data Structures (Java + JUnit).

Compiles each student submission together with the week's fixed JUnit tests,
runs the tests via the JUnit Platform Console Launcher, and writes one row
per student to a CSV: student_id, compiled, tests_passed, tests_total, score, notes.
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

BUILD_ROOT = Path("build_tmp")
OUTPUT_TRUNCATE_CHARS = 2000
OUTPUT_TRUNCATE_LINES = 40

TESTS_SUMMARY_RE = re.compile(
    r"\[\s*(\d+)\s+tests\s+(found|skipped|started|aborted|successful|failed)\s*\]"
)
PUBLIC_TYPE_RE = re.compile(
    r"public\s+(?:final\s+|abstract\s+)?(?:class|interface|enum|record)\s+(\w+)"
)


def resolve_java_filename(path: Path) -> str:
    """Java requires a public top-level type's filename to match its name.
    LMS downloads often rename single-file submissions to the student's ID
    (e.g. 87654321.java), which breaks that constraint even though the
    student's own code is otherwise fine. Detect the real public type name
    from the source and use THAT as the copied filename instead of trusting
    whatever the file was called on disk."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return path.name
    match = PUBLIC_TYPE_RE.search(text)
    if match:
        return f"{match.group(1)}.java"
    return path.name


def truncate(text: str) -> str:
    lines = text.splitlines()
    if len(lines) > OUTPUT_TRUNCATE_LINES:
        text = "\n".join(lines[:OUTPUT_TRUNCATE_LINES]) + "\n...[truncated]"
    if len(text) > OUTPUT_TRUNCATE_CHARS:
        text = text[:OUTPUT_TRUNCATE_CHARS] + "...[truncated]"
    return text.replace("\n", " | ").strip()


def find_junit_jar(lib_dir: Path) -> Path:
    jars = sorted(lib_dir.glob("*.jar"))
    if not jars:
        sys.exit(
            f"ERROR: no .jar found in {lib_dir}. Download the JUnit Platform "
            f"Console Launcher standalone jar into that folder (see lib/README.md)."
        )
    standalone = [j for j in jars if "console-standalone" in j.name]
    if len(standalone) == 1:
        return standalone[0]
    if len(standalone) > 1:
        sys.exit(
            f"ERROR: multiple console-standalone jars found in {lib_dir}: "
            f"{[j.name for j in standalone]}. Keep only one."
        )
    if len(jars) == 1:
        return jars[0]
    sys.exit(
        f"ERROR: multiple jars found in {lib_dir} and none named 'console-standalone': "
        f"{[j.name for j in jars]}. Keep only the JUnit console launcher jar there."
    )


def discover_submissions(
    submissions_dir: Path, extract_root: Path
) -> list[tuple[str, list[Path], list[str]]]:
    """Each result is (student_id, java_files, notes). A submission may be:
    - a folder (e.g. an unzipped Eclipse project - any nesting under it, .class/.classpath/
      bin/ etc. are simply ignored since only *.java is globbed)
    - a single loose .java file
    - a .zip file (e.g. an Eclipse project exported as-is) - extracted into
      extract_root/<student_id>/ and then scanned the same way as a folder submission.
    """
    results: list[tuple[str, list[Path], list[str]]] = []
    for entry in sorted(submissions_dir.iterdir()):
        if entry.is_dir():
            java_files = sorted(entry.rglob("*.java"))
            results.append((entry.name, java_files, []))
        elif entry.is_file() and entry.suffix == ".java":
            results.append((entry.stem, [entry], []))
        elif entry.is_file() and entry.suffix == ".zip":
            student_id = entry.stem
            extract_dir = extract_root / student_id
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True)
            try:
                with zipfile.ZipFile(entry) as zf:
                    zf.extractall(extract_dir)
            except zipfile.BadZipFile:
                results.append((student_id, [], [f"could not open {entry.name}: not a valid zip file"]))
                continue
            java_files = sorted(extract_dir.rglob("*.java"))
            notes = [] if java_files else [f"{entry.name} extracted OK but contained no .java files"]
            results.append((student_id, java_files, notes))
    return results


def discover_test_files(tests_dir: Path) -> list[Path]:
    test_files = sorted(tests_dir.glob("*.java"))
    if not test_files:
        sys.exit(f"ERROR: no .java test files found in {tests_dir}")
    return test_files


def prepare_build_dir(
    student_id: str, student_files: list[Path], test_files: list[Path], build_root: Path
) -> tuple[Path, list[str]]:
    notes: list[str] = []
    build_dir = build_root / student_id
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    test_names = {f.name for f in test_files}
    seen_names: set[str] = set()
    for src in student_files:
        dest_name = resolve_java_filename(src)
        if dest_name in test_names:
            notes.append(f"skipped student's {src.name} (colliding with official test file {dest_name})")
            continue
        if dest_name in seen_names:
            notes.append(f"skipped duplicate student file {src.name} (-> {dest_name})")
            continue
        seen_names.add(dest_name)
        shutil.copy2(src, build_dir / dest_name)

    for tf in test_files:
        shutil.copy2(tf, build_dir / tf.name)

    return build_dir, notes


@dataclass
class ProcResult:
    timed_out: bool
    returncode: int | None
    stdout: str = ""
    stderr: str = ""


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Force-kill proc and any children it spawned. subprocess's own
    proc.kill()/timeout handling is not reliable here: on Windows, a hung
    child that never produces output can leave communicate()'s internal
    reader thread blocked on a pipe read forever, even after the process
    is killed, if anything still holds the pipe's write handle open."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, timeout=10,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_with_hard_timeout(cmd: list[str], timeout: int) -> ProcResult:
    """Run cmd, capturing output via temp files (not pipes) so a hung,
    silent child can't deadlock a reader thread. Waits on the process
    handle only (no data to read), which enforces the timeout reliably,
    then force-kills the whole process tree if it's still alive."""
    with tempfile.TemporaryDirectory() as tmp:
        stdout_path = Path(tmp) / "stdout.txt"
        stderr_path = Path(tmp) / "stderr.txt"
        popen_kwargs = {}
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        with open(stdout_path, "w", encoding="utf-8") as out_f, \
             open(stderr_path, "w", encoding="utf-8") as err_f:
            proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, text=True, **popen_kwargs)
            try:
                returncode = proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                _kill_process_tree(proc)
                returncode = None
                timed_out = True

        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        return ProcResult(timed_out, returncode, stdout, stderr)


@dataclass
class CompileResult:
    success: bool
    classes_dir: Path
    output: str = ""


def compile_submission(build_dir: Path, junit_jar: Path, timeout: int) -> CompileResult:
    classes_dir = build_dir / "classes"
    classes_dir.mkdir(exist_ok=True)
    java_files = sorted(build_dir.glob("*.java"))

    cmd = [
        "javac",
        "-cp", str(junit_jar),
        "-d", str(classes_dir),
        "-encoding", "UTF-8",
        *[str(f) for f in java_files],
    ]
    result = run_with_hard_timeout(cmd, timeout)
    if result.timed_out:
        return CompileResult(False, classes_dir, "javac timed out")

    if result.returncode != 0:
        output = (result.stdout + result.stderr).replace(str(build_dir) + "\\", "").replace(str(build_dir) + "/", "")
        return CompileResult(False, classes_dir, truncate(output))
    return CompileResult(True, classes_dir)


TestRunResult = ProcResult


def run_tests(classes_dir: Path, junit_jar: Path, timeout: int) -> TestRunResult:
    cmd = [
        "java", "-jar", str(junit_jar),
        "execute",
        "--class-path", str(classes_dir),
        "--scan-classpath", str(classes_dir),
        "--disable-banner",
        "--disable-ansi-colors",
        "--details=summary",
    ]
    return run_with_hard_timeout(cmd, timeout)


@dataclass
class ParsedSummary:
    parse_ok: bool
    found: int = 0
    skipped: int = 0
    started: int = 0
    aborted: int = 0
    successful: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.found - self.skipped

    @property
    def passed(self) -> int:
        return self.successful


def parse_junit_output(stdout: str) -> ParsedSummary:
    counts: dict[str, int] = {}
    for value, label in TESTS_SUMMARY_RE.findall(stdout):
        counts[label] = int(value)

    required = {"found", "skipped", "started", "aborted", "successful", "failed"}
    if not required.issubset(counts.keys()):
        return ParsedSummary(parse_ok=False)

    return ParsedSummary(
        parse_ok=True,
        found=counts["found"],
        skipped=counts["skipped"],
        started=counts["started"],
        aborted=counts["aborted"],
        successful=counts["successful"],
        failed=counts["failed"],
    )


def grade_student(
    student_id: str,
    student_files: list[Path],
    discovery_notes: list[str],
    test_files: list[Path],
    junit_jar: Path,
    build_root: Path,
    timeout: int,
    keep_build: bool,
) -> dict:
    row = {
        "student_id": student_id,
        "compiled": "no",
        "tests_passed": 0,
        "tests_total": 0,
        "score": 0,
        "notes": "",
    }
    build_dir = None
    try:
        if not student_files:
            row["notes"] = "; ".join(discovery_notes + ["no .java source files found"]).strip("; ")
            return row

        build_dir, prep_notes = prepare_build_dir(student_id, student_files, test_files, build_root)
        prep_notes = discovery_notes + prep_notes

        compile_result = compile_submission(build_dir, junit_jar, timeout)
        if not compile_result.success:
            row["notes"] = "; ".join(prep_notes + [f"COMPILE ERROR: {compile_result.output}"]).strip("; ")
            return row

        run_result = run_tests(compile_result.classes_dir, junit_jar, timeout)
        if run_result.timed_out:
            row["compiled"] = "yes"
            row["notes"] = "; ".join(prep_notes + [f"test run timed out after {timeout}s"]).strip("; ")
            return row

        summary = parse_junit_output(run_result.stdout)
        row["compiled"] = "yes"
        if not summary.parse_ok:
            row["notes"] = "; ".join(
                prep_notes + [f"could not parse JUnit output: {truncate(run_result.stdout)}"]
            ).strip("; ")
            return row

        if summary.found == 0:
            row["notes"] = "; ".join(
                prep_notes
                + ["compiled OK but 0 tests found (student may have renamed/overwritten a class referenced by the test)"]
            ).strip("; ")
            return row

        row["tests_passed"] = summary.passed
        row["tests_total"] = summary.total
        row["score"] = summary.passed

        extra = []
        if summary.aborted:
            extra.append(f"{summary.aborted} test(s) aborted")
        if summary.failed:
            extra.append(f"{summary.failed} test(s) failed")
        row["notes"] = "; ".join(prep_notes + extra).strip("; ")
        return row

    except Exception as exc:  # noqa: BLE001 - top-level safety net, must never crash the batch
        row["notes"] = f"UNEXPECTED ERROR: {exc!r}"
        return row
    finally:
        if build_dir is not None and build_dir.exists() and not keep_build:
            shutil.rmtree(build_dir, ignore_errors=True)


def sort_rows(rows: list[dict]) -> list[dict]:
    def sort_key(row: dict):
        sid = row["student_id"]
        return (0, int(sid)) if sid.isdigit() else (1, sid)

    return sorted(rows, key=sort_key)


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sort_rows(rows)
    fieldnames = ["student_id", "compiled", "tests_passed", "tests_total", "score", "notes"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_sorted)


def write_scores_csv(rows: list[dict], out_path: Path) -> None:
    """Simple 2-column CSV (student_id, score) for gradebook upload."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sort_rows(rows)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["student_id", "score"])
        for row in rows_sorted:
            writer.writerow([row["student_id"], row["score"]])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-grade Java submissions with JUnit 5.")
    parser.add_argument("--submissions", default="submissions")
    parser.add_argument("--tests", default="tests")
    parser.add_argument("--lib", default="lib")
    parser.add_argument("--out", default=str(Path("results") / "grades.csv"),
                         help="detailed CSV: student_id, compiled, tests_passed, tests_total, score, notes")
    parser.add_argument("--scores-out", default=str(Path("results") / "scores.csv"),
                         help="simple CSV: student_id, score (e.g. for gradebook upload)")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--keep-build", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    submissions_dir = Path(args.submissions).resolve()
    tests_dir = Path(args.tests).resolve()
    lib_dir = Path(args.lib).resolve()
    out_path = Path(args.out).resolve()
    scores_out_path = Path(args.scores_out).resolve()
    build_root = BUILD_ROOT.resolve()

    if shutil.which("javac") is None:
        sys.exit("ERROR: javac not found on PATH. Install a JDK (not just a JRE).")
    if not submissions_dir.is_dir():
        sys.exit(f"ERROR: submissions folder not found: {submissions_dir}")
    if not tests_dir.is_dir():
        sys.exit(f"ERROR: tests folder not found: {tests_dir}")

    junit_jar = find_junit_jar(lib_dir)
    test_files = discover_test_files(tests_dir)

    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)
    extract_root = build_root / "_extracted"
    extract_root.mkdir(parents=True)

    submissions = discover_submissions(submissions_dir, extract_root)
    if not submissions:
        sys.exit(f"ERROR: no student submissions found in {submissions_dir}")

    print("Auto-Grader for Data Structures - starting run")
    print(f"  submissions: {submissions_dir}  ({len(submissions)} found)")
    print(f"  tests:       {tests_dir}  ({len(test_files)} test file(s))")
    print(f"  junit jar:   {junit_jar}")

    rows = []
    total = len(submissions)
    for i, (student_id, student_files, discovery_notes) in enumerate(submissions, start=1):
        row = grade_student(
            student_id, student_files, discovery_notes, test_files,
            junit_jar, build_root, args.timeout, args.keep_build
        )
        rows.append(row)
        if row["compiled"] == "no":
            status = "COMPILE ERROR" if "COMPILE ERROR" in row["notes"] else "NO SOURCE FILES"
            print(f"[{i}/{total}] {student_id}: {status} (score {row['score']})")
        else:
            print(
                f"[{i}/{total}] {student_id}: compiled, "
                f"{row['tests_passed']}/{row['tests_total']} tests passed (score {row['score']})"
            )

    if not args.keep_build and build_root.exists():
        shutil.rmtree(build_root, ignore_errors=True)

    write_csv(rows, out_path)
    write_scores_csv(rows, scores_out_path)

    compiled_count = sum(1 for r in rows if r["compiled"] == "yes")
    avg_score = sum(r["score"] for r in rows) / len(rows) if rows else 0.0
    zero_tests = sum(1 for r in rows if r["compiled"] == "yes" and r["tests_total"] == 0)
    timeouts = sum(1 for r in rows if "timed out" in r["notes"])

    print(f"\nDone. Wrote {len(rows)} rows to {out_path} and {scores_out_path}")
    print(
        f"  compiled: {compiled_count}/{len(rows)}   average score: {avg_score:.2f}   "
        f"0-tests-found: {zero_tests}   timeouts: {timeouts}"
    )


if __name__ == "__main__":
    main()
