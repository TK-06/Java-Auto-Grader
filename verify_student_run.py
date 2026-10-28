"""Self-check for a student: grade MY export using the JUnit test I bundled in it.

    1. Drop your exported .jar / .zip into submissions/
    2. python verify_student_run.py

This behaves like `python grade.py` - same unpacking, same structure checks, same
caps (no .class files = 50%, wrong question = 0, bare .java = 0, ...), same
compile and test run, same notes - with ONE difference: the tests are not the
hidden official ones. Instead it takes the JUnit test file(s) your own export
already contains (the given test you dropped into your project) and runs those
against your code. So a clean result here means "my export is packaged correctly
and passes the tests I was given"; it can not tell you how you do on tests you
have not seen.

Nothing under results/grades.csv or results/mcvScore.csv is touched - the
detailed result goes to results/verify_grades.csv.

Optional: --config <dir> loads rubric.json / structure.json / manual_review.json
/ starter/ from a folder (default: tests/) when it has them, so a TA can publish
a student-safe copy of those and get the matching checks and weights. Without
them, scoring is 1 point per test and the required classes are inferred from the
tests, exactly as grade.py does when those files are absent. Any *.java in that
folder is ignored: this script never uses test files other than your own.

Legacy TA mode (`--ide-layout`): the original behaviour of this script. Rebuilds
each student's project the way an IDE lays it out, copies the OFFICIAL
tests/*.java in and runs them with the working directory set to the project
root, to prove a test behaves the same on a student's machine as under
grade.py (see README, "Verifying a change before it ships"):

    python verify_student_run.py --ide-layout --grading . --work <scratch> ALL
"""
import re
import sys
import json
import shutil
import zipfile
import pathlib
import argparse
import subprocess
import xml.etree.ElementTree as ET

PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)


def dest_for(text, filename):
    """Where this file goes under src/, from its OWN package declaration.

    Deliberately ignores how the jar happens to lay the file out. Exports vary
    wildly - sources at the archive root, under src/, or under a nested
    <ProjectName>/src/ - and mirroring that layout both reproduces junk
    directories and blows past Windows' 260-character path limit once nested in
    a working directory. The package declaration is the thing javac and the JVM
    actually care about, so it is the thing that decides placement."""
    match = PACKAGE_RE.search(text)
    if not match:
        return pathlib.PurePosixPath(filename)
    return pathlib.PurePosixPath(match.group(1).replace(".", "/")) / filename


def ide_layout_main(args):
    """The original TA tool: official tests, student's IDE project layout."""
    if not (args.grading and args.work and args.students):
        sys.exit("ERROR: --ide-layout needs --grading, --work and at least one student id (or ALL)")

    # Absolute, because the test run below sets cwd to the student's project
    # directory - a relative jar or tests path would resolve against THAT and
    # silently find nothing.
    grading = pathlib.Path(args.grading).resolve()
    junit = next((grading / "lib").glob("junit-platform-console-standalone-*.jar"))
    test_files = sorted((grading / "tests").glob("*.java"))
    test_classes = []
    for tf in test_files:
        pkg = re.search(r"^\s*package\s+([\w.]+)\s*;", tf.read_text(encoding="utf-8"), re.M)
        test_classes.append(f"{pkg.group(1)}.{tf.stem}" if pkg else tf.stem)

    ids = args.students
    if ids == ["ALL"]:
        ids = sorted(p.stem for p in (grading / "submissions").glob("*.jar"))

    work = pathlib.Path(args.work).resolve()
    report = {}

    for sid in ids:
        jar = grading / "submissions" / f"{sid}.jar"
        if not jar.exists():
            report[sid] = {"status": "NO JAR"}
            continue
        proj = work / sid
        if proj.exists():
            shutil.rmtree(proj)
        proj.mkdir(parents=True)

        with zipfile.ZipFile(jar) as zf:
            names = [n for n in zf.namelist() if n.endswith(".java")]
            if not names:
                report[sid] = {"status": "NO .java IN JAR (student could not run tests either)"}
                continue
            for n in names:
                text = zf.read(n).decode("utf-8", "replace")
                dest = proj / "src" / dest_for(text, n.rsplit("/", 1)[-1])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")

        # The student is told to drop the given test file into their project;
        # its own package declaration decides where that is, same as above.
        for tf in test_files:
            text = tf.read_text(encoding="utf-8")
            dest = proj / "src" / dest_for(text, tf.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")

        out = proj / "out"
        out.mkdir()
        sources = [str(p) for p in (proj / "src").rglob("*.java")]
        comp = subprocess.run(["javac", "-cp", str(junit), "-d", str(out),
                               "-encoding", "UTF-8", *sources],
                              capture_output=True, text=True)
        if comp.returncode != 0:
            first = (comp.stdout + comp.stderr).strip().splitlines()
            report[sid] = {"status": "DID NOT COMPILE IN STUDENT LAYOUT",
                           "first_error": first[0] if first else "?"}
            continue

        passed = failed = 0
        failures = []
        errors = []
        for i, fqcn in enumerate(test_classes):
            rep = proj / "reports" / str(i)
            rep.mkdir(parents=True)
            # cwd = project root: exactly what an IDE Run does, and what any test
            # reading its own source via a relative path depends on.
            run = subprocess.run(["java", "-jar", str(junit), "execute",
                                  "--class-path", str(out), "--select-class", fqcn,
                                  "--reports-dir", str(rep), "--disable-banner",
                                  "--disable-ansi-colors", "--details=summary"],
                                 cwd=proj, capture_output=True, text=True)
            written = list(rep.rglob("TEST-*.xml"))
            if not written:
                # JUnit exits non-zero for a failing test too, so "no reports at
                # all" is the signal that the RUN itself broke - a bad classpath,
                # an unreadable jar. Report it instead of letting it read as a
                # clean "0 tests", which is indistinguishable from a pass.
                errors.append(f"{fqcn}: no report written "
                              f"({(run.stderr or run.stdout).strip().splitlines()[-1:] or ['?']})")
                continue
            for xml in written:
                for tc in ET.parse(xml).getroot().iter("testcase"):
                    name = re.sub(r"\(.*\)$", "", tc.get("name", ""))
                    bad = tc.find("failure") is not None or tc.find("error") is not None
                    if bad:
                        failed += 1
                        failures.append(f"{fqcn.split('.')[-1]}.{name}")
                    else:
                        passed += 1
        report[sid] = {"status": "TEST RUN BROKE" if errors else "ran",
                       "passed": passed, "failed": failed, "failures": failures}
        if errors:
            report[sid]["errors"] = errors

    print(json.dumps(report, indent=2))


# ---------------------------------------------------------------------------
# Student self-check: grade.py's pipeline, with the tests taken from the jar.
# ---------------------------------------------------------------------------

VERIFY_BUILD_ROOT = pathlib.Path("build_tmp_verify")


def bundled_test_files(java_files, wanted_names, grade):
    """The JUnit test file(s) the student's own export carries, as (dest_name, source).

    Detected by content (an `org.junit` import), not by name - there is no
    naming convention to rely on. Keyed by grade.resolve_java_filename so the
    name matches what grade.prepare_build_dir will compare against. When two
    files share a name the first wins, like prepare_build_dir does for sources."""
    found = {}
    for f in java_files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        if not grade.JUNIT_IMPORT_RE.search(text):
            continue
        name = grade.resolve_java_filename(f)
        if wanted_names and name not in wanted_names:
            continue
        found.setdefault(name, f)
    return found


def student_main(args):
    import grade  # same folder; imported here so --ide-layout stays standalone

    submissions_dir = pathlib.Path(args.submissions).resolve()
    config_dir = pathlib.Path(args.config).resolve()
    lib_dir = pathlib.Path(args.lib).resolve()
    out_path = pathlib.Path(args.out).resolve()
    build_root = VERIFY_BUILD_ROOT.resolve()
    failed_build_root = out_path.parent / "verify_failed_builds"

    if shutil.which("javac") is None:
        sys.exit("ERROR: javac not found on PATH. Install a JDK (not just a JRE).")
    if not submissions_dir.is_dir():
        sys.exit(f"ERROR: submissions folder not found: {submissions_dir}")
    problem = grade.check_output_writable(out_path)
    if problem is not None:
        sys.exit(f"ERROR: {problem}")

    junit_jar = grade.find_junit_jar(lib_dir)

    # Optional marking config. Loaded from a folder that is NOT where test files
    # come from, so a TA's hidden tests/*.java can never leak into this mode.
    if config_dir.is_dir():
        rubric = grade.load_rubric(config_dir)
        rubric_gates = grade.load_rubric_gates(config_dir)
        required_classes = grade.load_structure_baseline(config_dir)
        student_classes = grade.load_student_classes(config_dir)
        manual_review_checks = grade.load_manual_review_checks(config_dir)
        stub_starter_files = grade.load_stub_starter_files(config_dir)
    else:
        rubric = required_classes = manual_review_checks = stub_starter_files = None
        rubric_gates, student_classes = {}, set()

    for root in (build_root, failed_build_root):
        if root.exists():
            grade.rmtree_with_retry(root)
        root.mkdir(parents=True)
    extract_root = build_root / "_extracted"
    extract_root.mkdir()
    tests_root = build_root / "_tests"
    tests_root.mkdir()

    print(f"Self-check - unpacking submissions from {submissions_dir} ...")
    submissions = grade.discover_submissions(submissions_dir, extract_root)
    if not submissions:
        sys.exit(f"ERROR: nothing found in {submissions_dir} - drop your exported .jar or .zip there.")

    print("\nSelf-check: using the JUnit test file(s) bundled in YOUR export, not the hidden official tests.")
    print(f"  junit jar:  {junit_jar}")
    print(f"  marking:    rubric {'yes' if rubric is not None else 'no (1 point per test)'}, "
          f"structure.json {'yes' if required_classes is not None else 'no (required classes inferred from the tests)'}")
    print()

    wanted = set(args.test_file or [])
    rows = []
    total = len(submissions)
    for i, sub in enumerate(submissions, start=1):
        found = bundled_test_files(sub.java_files, wanted, grade)
        print(f"[{i}/{total}] {sub.student_id}:")

        if not found and not sub.not_an_archive:
            if wanted:
                why = f"none of the requested test file(s) {sorted(wanted)} are in the submission"
            else:
                why = ("no JUnit test file found in the submission (no .java file imports org.junit) - "
                       "put the given test file in your project, e.g. src/test/, and export again")
            row = {"student_id": sub.student_id, "compiled": "no", "tests_passed": 0,
                   "tests_total": 0, "score": 0, "max_score": 0, "uncapped_score": 0,
                   "score_cap": "", "passed_tests": "", "failed_tests": "",
                   "failure_details": "",
                   "notes": "; ".join(sub.notes + [why]).strip("; ")}
            rows.append(row)
            print(f"         NO TEST FILE (score 0) - {why}")
            continue

        # One private tests folder per submission: its own bundled tests become
        # the "official" ones for this single grade_student call, so
        # prepare_build_dir swaps in an identical copy and every downstream check
        # (class inference, wrong-submission, structure, caps) runs unchanged.
        tests_dir = tests_root / str(i)
        tests_dir.mkdir()
        test_files = []
        for name, src_file in sorted(found.items()):
            dest = tests_dir / name
            shutil.copy2(src_file, dest)
            test_files.append(dest)
        test_classes = [grade.test_class_fqcn(tf) for tf in test_files]
        candidates = grade.collect_required_class_names(test_files) | set(required_classes or [])

        if test_files:
            print(f"         tests taken from your export: "
                  f"{', '.join(tf.name for tf in test_files)}")

        row = grade.grade_student(
            sub.student_id, str(i), sub.java_files, sub.notes, test_files, test_classes,
            junit_jar, build_root, args.timeout, args.keep_build, rubric, required_classes,
            failed_build_root,
            class_search_root=sub.class_search_root,
            zip_needed_deeper_extraction=sub.zip_needed_deeper_extraction,
            class_fallback_candidates=candidates,
            not_an_archive=sub.not_an_archive,
            manual_review_checks=manual_review_checks,
            stub_starter_files=stub_starter_files,
            rubric_gates=rubric_gates,
            student_classes=student_classes,
            no_compiled_classes=sub.no_compiled_classes,
        )
        rows.append(row)

        if row["compiled"] == "no":
            if "STRUCTURE ERROR" in row["notes"]:
                status = "STRUCTURE ERROR"
            elif grade.WRONG_SUBMISSION_PREFIX in row["notes"]:
                status = "WRONG SUBMISSION"
            elif "COMPILE ERROR" in row["notes"]:
                status = "COMPILE ERROR"
            else:
                status = "NO SOURCE FILES"
            reason = grade.console_failure_reason(row)
            print(f"         {status} (score {row['score']})" + (f" - {reason}" if reason else ""))
        else:
            print(f"         compiled, {row['tests_passed']}/{row['tests_total']} tests passed "
                  f"(score {row['score']:g}/{row['max_score']:g}){grade.console_line_suffix(row)}")
        if row["failed_tests"]:
            print(f"         failed: {row['failed_tests']}")
        if args.keep_build:
            print(f"         build dir: {build_root / str(i)}")

    if not args.keep_build and build_root.exists():
        grade.rmtree_quietly(build_root)

    grade.write_csv(rows, out_path)
    print(f"\nDone. Detail for every row (including the full notes) is in {out_path}")
    if any(failed_build_root.iterdir()):
        print(f"  Build folder(s) of anything that failed to compile: {failed_build_root}")


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submissions", default="submissions",
                    help="folder holding your exported .jar/.zip (default: submissions)")
    ap.add_argument("--lib", default="lib", help="folder holding the JUnit console jar (default: lib)")
    ap.add_argument("--config", default="tests",
                    help="optional folder with rubric.json / structure.json / manual_review.json / "
                         "starter/ - its *.java files are NEVER used (default: tests)")
    ap.add_argument("--test-file", action="append", metavar="NAME.java",
                    help="only run this bundled test file (repeatable). Use it when your export "
                         "also carries old test files from earlier weeks")
    ap.add_argument("--out", default=str(pathlib.Path("results") / "verify_grades.csv"),
                    help="detailed CSV (default: results/verify_grades.csv)")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--keep-build", action="store_true",
                    help="keep build_tmp_verify/ so you can inspect what was compiled")
    # Original TA behaviour, kept under its own flag.
    ap.add_argument("--ide-layout", action="store_true",
                    help="TA mode: rebuild each project in IDE layout and run the OFFICIAL tests")
    ap.add_argument("--grading", help="(--ide-layout) the grading/ directory")
    ap.add_argument("--work", help="(--ide-layout) scratch directory to build projects in")
    ap.add_argument("students", nargs="*", help="(--ide-layout) student id(s), or ALL")
    return ap.parse_args()


def main():
    args = parse_args()
    if args.ide_layout:
        ide_layout_main(args)
    else:
        if args.students:
            sys.exit("ERROR: student ids are only used with --ide-layout. Drop your file into "
                     "submissions/ and run with no arguments.")
        student_main(args)


if __name__ == "__main__":
    main()
