#!/usr/bin/env python3
"""Auto-Grader for Data Structures (Java + JUnit).

Compiles each student submission together with the week's fixed JUnit tests,
runs each official test class in its own isolated JVM invocation via the
JUnit Platform Console Launcher, and writes one row per student to a CSV:
student_id, compiled, tests_passed, tests_total, score, max_score,
passed_tests, failed_tests, failure_details, student_submitted_tests_passed,
student_submitted_tests_total, student_submitted_failed_tests,
student_submitted_failure_details, notes. Score is 1 point per passed test by
default, or a weighted sum if tests/rubric.json is present. failure_details
carries the JUnit assertion message for each failed test (e.g. "expected:
<0> but was: <-1>"), so a failure can be understood straight from the CSV
instead of re-reading the test's source. student_submitted_* columns report
any leftover JUnit test classes still in the student's submission (e.g. from
an earlier week) - run isolated for visibility, never counted toward the
score. A submission that fails to compile has its extracted+flattened build
directory preserved under results/failed_builds/<student_id>__<n>/ for
manual review, regardless of --keep-build.
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

BUILD_ROOT = Path("build_tmp")
OUTPUT_TRUNCATE_CHARS = 2000
OUTPUT_TRUNCATE_LINES = 40

PUBLIC_TYPE_RE = re.compile(
    r"public\s+(?:final\s+|abstract\s+)?(?:class|interface|enum|record)\s+(\w+)"
)
METHOD_NAME_RE = re.compile(r"^\w+")
PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
JUNIT_IMPORT_RE = re.compile(r"^\s*import\s+org\.junit\b", re.MULTILINE)


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


def strip_package_declaration(
    text: str, keep_packages: set[str] | None = None
) -> tuple[str, str | None, str | None]:
    """Some weeks' official tests (e.g. Bot/Part) assume every student class
    sits in the default, unnamed package. Other weeks' official tests
    explicitly `import application.CPTSMachine;` etc., meaning THAT package
    is required, not accidental. Blindly flattening every student package
    broke the second case: a correctly-structured submission stopped
    compiling because its own official test still imported the now-gone
    package. So the caller must tell us which package names the official
    tests actually reference (see collect_referenced_packages) - anything
    in that set is left untouched.

    A declared package that ISN'T an exact match may still be a required one
    sitting under an extra prefix - e.g. an IDE inferring
    `Q1_toStudent.application` from a source-root folder literally named
    after the assignment, when the official tests require exactly
    `application`. Deleting the declaration in that case still leaves
    `import application.Foo;` (in the official tests, or in a leftover
    student test file written against the same required package) unable to
    resolve, so instead the declaration is rewritten down to the canonical
    required name. Only when a package matches NO required package, even as
    a suffix, is it safe to assume it's a pure IDE artifact (e.g. IntelliJ
    inferring `package main.java;` from an unmarked "main/java" source
    folder) rather than a real project requirement, and stripped entirely so
    the unnamed-package test can see the class unqualified.

    Returns (possibly-modified text, the declared package name if it was
    acted on at all, the canonical name it was rewritten to - or None if it
    was stripped to the unnamed package instead of rewritten)."""
    keep_packages = keep_packages or set()
    match = PACKAGE_RE.search(text)
    if not match:
        return text, None, None
    declared = match.group(1)
    if declared in keep_packages:
        return text, None, None

    canonical = max(
        (kp for kp in keep_packages if declared.endswith("." + kp)),
        key=len,
        default=None,
    )
    if canonical is not None:
        new_text = PACKAGE_RE.sub(f"package {canonical};", text, count=1)
        return new_text, declared, canonical

    return PACKAGE_RE.sub("", text, count=1), declared, None


IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\.(?:\w+|\*)\s*;\s*\n?", re.MULTILINE)


def collect_referenced_packages(test_files: list[Path]) -> set[str]:
    """Package names the official test files themselves declare or import
    from - see strip_package_declaration for why this matters. Reads each
    test file's own package declaration (a test in `package logic;` needs
    same-package access to a student's `logic` classes) plus every package
    named in an `import pkg.Thing;` line."""
    referenced: set[str] = set()
    for tf in test_files:
        text = tf.read_text(encoding="utf-8", errors="ignore")
        pkg_match = PACKAGE_RE.search(text)
        if pkg_match:
            referenced.add(pkg_match.group(1))
        referenced.update(m.group(1) for m in IMPORT_RE.finditer(text))
    return referenced


def strip_imports_of_packages(text: str, package_names: set[str]) -> str:
    """Once strip_package_declaration has flattened a submission's classes
    into the unnamed package, any `import <pkg>.Foo;` elsewhere in that same
    submission naming one of those now-gone packages (e.g. a student's own
    TestBot.java doing `import main.java.Bot;` because Bot.java used to
    declare `package main.java;`) is a compile error, not just dead code:
    Java doesn't allow importing from the unnamed package at all - same-
    package types are simply visible without an import. Every other import
    (java.util.*, org.junit.*, an unrelated package) is left untouched."""
    if not package_names:
        return text

    def _drop(match: re.Match) -> str:
        return "" if match.group(1) in package_names else match.group(0)

    return IMPORT_RE.sub(_drop, text)


def rewrite_imports_of_renamed_packages(text: str, renamed_packages: dict[str, str]) -> str:
    """Companion to strip_imports_of_packages for the other outcome of
    strip_package_declaration: a package that was rewritten down to its
    canonical required name (e.g. `Q1_toStudent.logic` -> `logic`) still
    exists as a real, named package - unlike the fully-stripped case, a
    sibling file's `import Q1_toStudent.logic.Station;` can't just be
    dropped (that would assume unnamed-package visibility, which no longer
    applies); it must be rewritten to `import logic.Station;` instead."""
    if not renamed_packages:
        return text

    def _rename(match: re.Match) -> str:
        old_pkg = match.group(1)
        new_pkg = renamed_packages.get(old_pkg)
        if new_pkg is None:
            return match.group(0)
        return match.group(0).replace(f"{old_pkg}.", f"{new_pkg}.", 1)

    return IMPORT_RE.sub(_rename, text)


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
    - a .zip or .jar file (a JAR is just a ZIP file with a manifest, so the same extraction
      works for both - e.g. an Eclipse project exported as a zip, or exported as a runnable
      JAR with sources included) - extracted into extract_root/<n>/ (a plain sequential
      counter, NOT the student_id: real submission filenames can be arbitrarily long -
      LMS downloads, browser dedup suffixes, etc. - and combined with this repo's own path
      depth plus a jar's internal package structure, that reliably blows past Windows'
      260-character path limit during extraction) and then scanned like a folder submission.
    """
    results: list[tuple[str, list[Path], list[str]]] = []
    for idx, entry in enumerate(sorted(submissions_dir.iterdir())):
        if entry.is_dir():
            java_files = sorted(entry.rglob("*.java"))
            results.append((entry.name, java_files, []))
        elif entry.is_file() and entry.suffix == ".java":
            results.append((entry.stem, [entry], []))
        elif entry.is_file() and entry.suffix in (".zip", ".jar"):
            student_id = entry.stem
            extract_dir = extract_root / str(idx)
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True)
            try:
                with zipfile.ZipFile(entry) as zf:
                    zf.extractall(extract_dir)
            except zipfile.BadZipFile:
                results.append((student_id, [], [f"could not open {entry.name}: not a valid zip/jar file"]))
                continue
            except OSError as exc:
                # e.g. Windows path-length limit blown by a long filename/deeply
                # nested entry inside the archive - must not crash the whole batch.
                results.append((student_id, [], [f"could not extract {entry.name}: {exc}"]))
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


def test_class_fqcn(path: Path) -> str:
    """Fully-qualified class name (package.ClassName) for a test file, used
    with --select-class to run it in its own JVM invocation. JUnit 5 test
    classes don't need to be public, so this trusts the filename for the
    class name (matching Java's own requirement that a top-level type's
    filename match its name) rather than requiring a "public" modifier match."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    pkg_match = PACKAGE_RE.search(text)
    class_name = path.stem
    if pkg_match:
        return f"{pkg_match.group(1)}.{class_name}"
    return class_name


def find_extra_test_files(build_dir: Path, official_names: set[str]) -> list[Path]:
    """.java files sitting directly in build_dir that aren't one of the
    official tests/ files but do import JUnit - i.e. a student's own
    leftover test class from a prior week (e.g. TestCPTSMachine.java sitting
    next to this week's official TestCPTSMachine2.java). Detected by content
    rather than filename, since there's no naming convention to rely on.
    Works on source text alone, so it can run either before compiling (to
    decide what to exclude on a fallback compile - see
    compile_submission_with_fallback) or after (see
    discover_extra_test_classes, run separately from the official tests so
    it can't share static state with them or count toward the score)."""
    extra: list[Path] = []
    for java_file in sorted(build_dir.glob("*.java")):
        if java_file.name in official_names:
            continue
        text = java_file.read_text(encoding="utf-8", errors="ignore")
        if JUNIT_IMPORT_RE.search(text):
            extra.append(java_file)
    return extra


def discover_extra_test_classes(build_dir: Path, official_names: set[str]) -> list[str]:
    """FQCNs of find_extra_test_files, for --select-class - see that
    function for what counts as an "extra" test class and why."""
    return [test_class_fqcn(f) for f in find_extra_test_files(build_dir, official_names)]


def prepare_build_dir(
    build_key: str, student_files: list[Path], test_files: list[Path], build_root: Path
) -> tuple[Path, list[str]]:
    notes: list[str] = []
    build_dir = build_root / build_key
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    test_names = {f.name for f in test_files}
    referenced_packages = collect_referenced_packages(test_files)
    seen_names: set[str] = set()
    kept: list[tuple[str, str]] = []  # (dest_name, text)
    stripped_package_names: set[str] = set()
    renamed_package_names: dict[str, str] = {}
    for src in student_files:
        dest_name = resolve_java_filename(src)
        if dest_name == "Main.java":
            # Excluded by course policy: recent IntelliJ project templates auto-generate
            # a scaffold Main.java (JEP 445 "instance main method" preview syntax) that
            # students often never touch or delete. It isn't part of any assignment and
            # its preview syntax fails plain javac, which would otherwise fail the whole
            # submission over an irrelevant leftover file.
            notes.append(f"skipped student's {src.name} (Main.java is excluded from grading)")
            continue
        if dest_name in test_names:
            notes.append(f"skipped student's {src.name} (colliding with official test file {dest_name})")
            continue
        if dest_name in seen_names:
            notes.append(f"skipped duplicate student file {src.name} (-> {dest_name})")
            continue
        seen_names.add(dest_name)
        text = src.read_text(encoding="utf-8", errors="ignore")
        text, declared_package, rewritten_to = strip_package_declaration(text, referenced_packages)
        if declared_package and rewritten_to:
            renamed_package_names[declared_package] = rewritten_to
            notes.append(
                f"rewrote package declaration '{declared_package}' to '{rewritten_to}' in {src.name} "
                f"(nested under an extra prefix, but the official tests require exactly '{rewritten_to}')"
            )
        elif declared_package:
            stripped_package_names.add(declared_package)
            notes.append(
                f"stripped package declaration '{declared_package}' from {src.name} "
                f"(this grader compiles everything in the unnamed package)"
            )
        kept.append((dest_name, text))

    # Second pass: now that every kept file's OWN package has been resolved
    # (collected above), fix up `import <thatpackage>.Foo;` lines elsewhere -
    # even in files whose own package was different or absent - to match:
    # drop the import for a package that's now unnamed (stripped_package_names),
    # or rewrite it to the canonical name for a package that just moved
    # (renamed_package_names). Must run after the first pass: a file can't
    # know how other packages in the submission were resolved until every
    # other file has been read.
    for dest_name, text in kept:
        text = strip_imports_of_packages(text, stripped_package_names)
        text = rewrite_imports_of_renamed_packages(text, renamed_package_names)
        (build_dir / dest_name).write_text(text, encoding="utf-8")

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


def compile_submission_with_fallback(
    build_dir: Path, junit_jar: Path, timeout: int, official_names: set[str]
) -> tuple[CompileResult, list[str]]:
    """Try the normal full compile first. If it fails AND the submission
    contains leftover, never-scored student test file(s) (see
    find_extra_test_files - e.g. a prior week's TestCPTSMachine.java sitting
    next to this week's official TestCPTSMachine2.java), retry once with
    just those files excluded. Every student .java file is compiled together
    in one javac invocation, so today a single broken leftover test class -
    which was never going to count toward the score anyway - can zero out an
    otherwise fully-working submission. Main.java is already excluded from
    grading for the same class of reason (see prepare_build_dir); this
    extends the same idea to leftover test files, but only as a recovery
    path: if excluding them does NOT make the submission compile, the
    ORIGINAL compile error is reported, not the retry's - excluding files is
    for recovering a submission, never for hiding a real compile error in
    the student's actual code or the official tests."""
    notes: list[str] = []
    compile_result = compile_submission(build_dir, junit_jar, timeout)
    if compile_result.success:
        return compile_result, notes

    extra_files = find_extra_test_files(build_dir, official_names)
    if not extra_files:
        return compile_result, notes

    original_error = compile_result.output
    excluded_dir = build_dir / "_excluded_extra"
    excluded_dir.mkdir(exist_ok=True)
    for f in extra_files:
        shutil.move(str(f), str(excluded_dir / f.name))

    retry_result = compile_submission(build_dir, junit_jar, timeout)
    if retry_result.success:
        names = ", ".join(f.name for f in extra_files)
        notes.append(
            f"excluded student's leftover test file(s) {names} (failed to compile on "
            f"their own and aren't part of this week's rubric) so the official tests "
            f"could still run; original compile error before exclusion: {original_error}"
        )
        return retry_result, notes

    # Excluding them didn't help - something else is actually broken, so put
    # the files back (for --keep-build inspection) and report the ORIGINAL
    # error rather than the retry's.
    for f in extra_files:
        shutil.move(str(excluded_dir / f.name), str(f))
    return compile_result, notes


TestRunResult = ProcResult


def run_tests(
    classes_dir: Path, reports_dir: Path, junit_jar: Path, timeout: int, test_classes: list[str]
) -> TestRunResult:
    """Run each official test class as its own JVM invocation (--select-class)
    rather than one --scan-classpath call across everything compiled. This
    matters because student code under test often keeps state in static
    fields: a single shared JVM lets one test class's run leak state into the
    next, including from a leftover test file a student never deleted from an
    earlier week (it still gets compiled since student source is compiled
    together, but is simply never selected/run here). Running each official
    class fresh matches what a student sees running one test class at a time
    in their IDE, and keeps every graded run's state isolated to just that
    class - exactly the scope tests/rubric.json expects."""
    stdout_parts = []
    stderr_parts = []
    for i, fqcn in enumerate(test_classes):
        # The console launcher names report files by test ENGINE
        # (TEST-junit-jupiter.xml), not by class, so every --select-class
        # invocation here would overwrite the previous one's report if they
        # shared a --reports-dir. Each call gets its own subdirectory instead;
        # collect_test_results() walks all of them recursively.
        class_reports_dir = reports_dir / str(i)
        class_reports_dir.mkdir(exist_ok=True)
        cmd = [
            "java", "-jar", str(junit_jar),
            "execute",
            "--class-path", str(classes_dir),
            "--select-class", fqcn,
            "--reports-dir", str(class_reports_dir),
            "--disable-banner",
            "--disable-ansi-colors",
            "--details=summary",
        ]
        result = run_with_hard_timeout(cmd, timeout)
        stdout_parts.append(f"--- {fqcn} ---\n{result.stdout}")
        stderr_parts.append(result.stderr)
        if result.timed_out:
            return ProcResult(True, None, "\n".join(stdout_parts), "\n".join(stderr_parts))
    return ProcResult(False, 0, "\n".join(stdout_parts), "\n".join(stderr_parts))


@dataclass
class TestCase:
    classname: str  # simple name, package prefix stripped (e.g. "TestStation2")
    method: str      # trailing "()"/params stripped (e.g. "testSetName")
    status: str       # "passed" | "failed" | "skipped"
    detail: str = ""  # failed/errored only: e.g. "expected: <0> but was: <-1>"


def collect_test_results(reports_dir: Path) -> list[TestCase]:
    """Parse every TEST-*.xml report the console launcher wrote (one per test
    engine per --select-class run, each in its own subdirectory - see
    run_tests) into a flat list of per-test results. XML reports are used
    instead of the printed text summary because they give per-test
    names/outcomes, which the summary block doesn't - needed for
    rubric-weighted scoring.

    For a failed/errored test, the <failure>/<error> element's own `message`
    attribute (JUnit's assertion library fills this with e.g. "expected:
    <0> but was: <-1>") is captured as `detail` - this is the one piece of
    the console launcher's output that actually says WHY a test failed, as
    opposed to just which one did. Falls back to the first line of the
    exception's stack trace when a message attribute isn't present (e.g. an
    exception thrown without one)."""
    results: list[TestCase] = []
    for report_file in sorted(reports_dir.rglob("TEST-*.xml")):
        tree = ET.parse(report_file)
        for testcase in tree.getroot().findall("testcase"):
            classname = testcase.get("classname", "").rsplit(".", 1)[-1]
            method_match = METHOD_NAME_RE.match(testcase.get("name", ""))
            method = method_match.group(0) if method_match else testcase.get("name", "")
            failure = testcase.find("failure")
            if failure is None:
                failure = testcase.find("error")
            detail = ""
            if testcase.find("skipped") is not None:
                status = "skipped"
            elif failure is not None:
                status = "failed"
                detail = failure.get("message") or ""
                if not detail and failure.text:
                    detail = failure.text.strip().splitlines()[0]
            else:
                status = "passed"
            results.append(TestCase(classname, method, status, detail))
    return results


def load_rubric(tests_dir: Path) -> dict[str, dict[str, float]] | None:
    """Optional tests/rubric.json: {"ClassName": {"testMethod": points, ...}, ...}.
    When present, score becomes the weighted sum of passed tests found in the
    rubric instead of a flat 1-point-per-test count. Absent by default so weeks
    without a rubric behave exactly as before."""
    rubric_path = tests_dir / "rubric.json"
    if not rubric_path.exists():
        return None
    with open(rubric_path, encoding="utf-8") as f:
        return json.load(f)


def grade_student(
    student_id: str,
    build_key: str,
    student_files: list[Path],
    discovery_notes: list[str],
    test_files: list[Path],
    test_classes: list[str],
    junit_jar: Path,
    build_root: Path,
    timeout: int,
    keep_build: bool,
    rubric: dict[str, dict[str, float]] | None,
    failed_build_root: Path | None = None,
) -> dict:
    row = {
        "student_id": student_id,
        "compiled": "no",
        "tests_passed": 0,
        "tests_total": 0,
        "score": 0,
        "max_score": 0,
        "passed_tests": "",
        "failed_tests": "",
        "failure_details": "",
        "student_submitted_tests_passed": 0,
        "student_submitted_tests_total": 0,
        "student_submitted_failed_tests": "",
        "student_submitted_failure_details": "",
        "notes": "",
    }
    build_dir = None
    try:
        if not student_files:
            row["notes"] = "; ".join(discovery_notes + ["no .java source files found"]).strip("; ")
            return row

        build_dir, prep_notes = prepare_build_dir(build_key, student_files, test_files, build_root)
        prep_notes = discovery_notes + prep_notes

        official_names = {f.name for f in test_files}
        compile_result, fallback_notes = compile_submission_with_fallback(
            build_dir, junit_jar, timeout, official_names
        )
        prep_notes = prep_notes + fallback_notes
        if not compile_result.success:
            row["notes"] = "; ".join(prep_notes + [f"COMPILE ERROR: {compile_result.output}"]).strip("; ")
            return row

        reports_dir = build_dir / "reports"
        reports_dir.mkdir(exist_ok=True)
        run_result = run_tests(compile_result.classes_dir, reports_dir, junit_jar, timeout, test_classes)
        if run_result.timed_out:
            row["compiled"] = "yes"
            row["notes"] = "; ".join(prep_notes + [f"test run timed out after {timeout}s"]).strip("; ")
            return row

        row["compiled"] = "yes"
        try:
            test_cases = collect_test_results(reports_dir)
        except ET.ParseError as exc:
            row["notes"] = "; ".join(
                prep_notes + [f"could not parse JUnit XML reports ({exc}): {truncate(run_result.stdout)}"]
            ).strip("; ")
            return row

        if not test_cases:
            row["notes"] = "; ".join(
                prep_notes
                + ["compiled OK but 0 tests found (student may have renamed/overwritten a class referenced by the test)"]
            ).strip("; ")
            return row

        passed = [tc for tc in test_cases if tc.status == "passed"]
        failed = [tc for tc in test_cases if tc.status == "failed"]
        skipped = [tc for tc in test_cases if tc.status == "skipped"]

        row["tests_passed"] = len(passed)
        row["tests_total"] = len(passed) + len(failed)
        row["passed_tests"] = "; ".join(f"{tc.classname}.{tc.method}" for tc in passed)
        row["failed_tests"] = "; ".join(f"{tc.classname}.{tc.method}" for tc in failed)
        row["failure_details"] = "; ".join(
            f"{tc.classname}.{tc.method}: {tc.detail}" for tc in failed if tc.detail
        )

        extra = []
        if skipped:
            extra.append(f"{len(skipped)} test(s) skipped")
        if failed:
            extra.append(f"{len(failed)} test(s) failed")

        if rubric is None:
            row["score"] = row["tests_passed"]
            row["max_score"] = row["tests_total"]
        else:
            found = {(tc.classname, tc.method) for tc in test_cases}
            passed_set = {(tc.classname, tc.method) for tc in passed}
            score = 0.0
            max_score = 0.0
            missing = []
            for classname, methods in rubric.items():
                for method, points in methods.items():
                    max_score += points
                    if (classname, method) in passed_set:
                        score += points
                    elif (classname, method) not in found:
                        missing.append(f"{classname}.{method}")
            row["score"] = score
            row["max_score"] = max_score
            if missing:
                extra.append(f"rubric test(s) not found in results: {', '.join(missing)}")
            rubric_keys = {(c, m) for c, ms in rubric.items() for m in ms}
            extras_found = found - rubric_keys
            if extras_found:
                extra.append(
                    "extra test(s) not in rubric (not scored): "
                    + ", ".join(f"{c}.{m}" for c, m in sorted(extras_found))
                )

        # Leftover test classes from the student's own submission (e.g. an old
        # TestCPTSMachine.java sitting next to this week's TestCPTSMachine2.java)
        # are compiled but never --select-class'd above, so they never affect
        # the official score. Still run them here - in their own isolated JVM
        # per class, same as the official run - purely so their results are
        # visible; never counted toward tests_passed/tests_total/score.
        extra_test_classes = discover_extra_test_classes(build_dir, {f.name for f in test_files})
        if extra_test_classes:
            extra_reports_dir = build_dir / "reports_extra"
            extra_reports_dir.mkdir(exist_ok=True)
            extra_run_result = run_tests(
                compile_result.classes_dir, extra_reports_dir, junit_jar, timeout, extra_test_classes
            )
            if extra_run_result.timed_out:
                extra.append("student-submitted leftover test file(s) timed out (not scored)")
            else:
                try:
                    extra_cases = collect_test_results(extra_reports_dir)
                except ET.ParseError:
                    extra_cases = []
                if extra_cases:
                    extra_passed = [tc for tc in extra_cases if tc.status == "passed"]
                    extra_failed = [tc for tc in extra_cases if tc.status == "failed"]
                    row["student_submitted_tests_passed"] = len(extra_passed)
                    row["student_submitted_tests_total"] = len(extra_cases)
                    row["student_submitted_failed_tests"] = "; ".join(
                        f"{tc.classname}.{tc.method}" for tc in extra_failed
                    )
                    row["student_submitted_failure_details"] = "; ".join(
                        f"{tc.classname}.{tc.method}: {tc.detail}" for tc in extra_failed if tc.detail
                    )
                    extra.append(
                        f"{len(extra_passed)}/{len(extra_cases)} student-submitted leftover test(s) "
                        f"found (not part of this week's rubric), run isolated (not scored)"
                    )

        row["notes"] = "; ".join(prep_notes + extra).strip("; ")
        return row

    except Exception as exc:  # noqa: BLE001 - top-level safety net, must never crash the batch
        row["notes"] = f"UNEXPECTED ERROR: {exc!r}"
        return row
    finally:
        if build_dir is not None and build_dir.exists():
            if row["compiled"] == "no" and failed_build_root is not None:
                # Preserved regardless of --keep-build: submissions/ is
                # typically cleared out shortly after grading (privacy,
                # disk space), which is exactly when a TA is most likely to
                # want to open the actual file that failed to compile. Keyed
                # by build_key too, not just student_id, so two submissions
                # that resolve to the same student_id (see the duplicate-id
                # warning in main()) don't overwrite each other's copy.
                audit_dir = failed_build_root / f"{student_id}__{build_key}"
                if audit_dir.exists():
                    shutil.rmtree(audit_dir, ignore_errors=True)
                shutil.copytree(build_dir, audit_dir)
            if not keep_build:
                shutil.rmtree(build_dir, ignore_errors=True)


def sort_rows(rows: list[dict]) -> list[dict]:
    def sort_key(row: dict):
        sid = row["student_id"]
        return (0, int(sid)) if sid.isdigit() else (1, sid)

    return sorted(rows, key=sort_key)


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sort_rows(rows)
    fieldnames = [
        "student_id", "compiled", "tests_passed", "tests_total", "score", "max_score",
        "passed_tests", "failed_tests", "failure_details", "student_submitted_tests_passed",
        "student_submitted_tests_total", "student_submitted_failed_tests",
        "student_submitted_failure_details", "notes",
    ]
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
                         help="detailed CSV: student_id, compiled, tests_passed, tests_total, "
                              "score, max_score, passed_tests, failed_tests, failure_details, "
                              "student_submitted_tests_passed, student_submitted_tests_total, "
                              "student_submitted_failed_tests, student_submitted_failure_details, "
                              "notes")
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
    failed_build_root = out_path.parent / "failed_builds"

    if shutil.which("javac") is None:
        sys.exit("ERROR: javac not found on PATH. Install a JDK (not just a JRE).")
    if not submissions_dir.is_dir():
        sys.exit(f"ERROR: submissions folder not found: {submissions_dir}")
    if not tests_dir.is_dir():
        sys.exit(f"ERROR: tests folder not found: {tests_dir}")

    junit_jar = find_junit_jar(lib_dir)
    test_files = discover_test_files(tests_dir)
    test_classes = [test_class_fqcn(tf) for tf in test_files]
    rubric = load_rubric(tests_dir)

    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)
    extract_root = build_root / "_extracted"
    extract_root.mkdir(parents=True)

    if failed_build_root.exists():
        shutil.rmtree(failed_build_root)
    failed_build_root.mkdir(parents=True)

    submissions = discover_submissions(submissions_dir, extract_root)
    if not submissions:
        sys.exit(f"ERROR: no student submissions found in {submissions_dir}")

    id_counts = Counter(student_id for student_id, _, _ in submissions)
    duplicate_ids = [sid for sid, count in id_counts.items() if count > 1]
    if duplicate_ids:
        print(
            "WARNING: multiple submissions resolved to the same student_id - "
            "both will be graded as separate rows in the CSV:"
        )
        for sid in duplicate_ids:
            print(f"  {sid}  ({id_counts[sid]} submissions)")
        print()

    print("Auto-Grader for Data Structures - starting run")
    print(f"  submissions: {submissions_dir}  ({len(submissions)} found)")
    print(f"  tests:       {tests_dir}  ({len(test_files)} test file(s))")
    print(f"  junit jar:   {junit_jar}")
    if rubric is not None:
        rubric_total = sum(points for methods in rubric.values() for points in methods.values())
        print(f"  rubric:      {tests_dir / 'rubric.json'}  (weighted, {rubric_total:g} points total)")
    else:
        print("  rubric:      none (tests/rubric.json not found - scoring 1 point per test)")

    rows = []
    total = len(submissions)
    for i, (student_id, student_files, discovery_notes) in enumerate(submissions, start=1):
        build_key = str(i)
        row = grade_student(
            student_id, build_key, student_files, discovery_notes, test_files, test_classes,
            junit_jar, build_root, args.timeout, args.keep_build, rubric, failed_build_root
        )
        rows.append(row)
        if row["compiled"] == "no":
            status = "COMPILE ERROR" if "COMPILE ERROR" in row["notes"] else "NO SOURCE FILES"
            print(f"[{i}/{total}] {student_id}: {status} (score {row['score']})")
        else:
            print(
                f"[{i}/{total}] {student_id}: compiled, "
                f"{row['tests_passed']}/{row['tests_total']} tests passed "
                f"(score {row['score']:g}/{row['max_score']:g})"
            )
        if args.keep_build:
            print(f"         build dir: {build_root / build_key}")

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
    failed_count = len(rows) - compiled_count
    if failed_count:
        print(f"  {failed_count} submission(s) failed to compile - build dir(s) saved under {failed_build_root}")


if __name__ == "__main__":
    main()
