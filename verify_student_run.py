"""Simulate a STUDENT running this week's official tests in their own project.

Not the same question as `python grade.py`. grade.py flattens a submission and
compiles it its own way; a student instead opens the project they exported,
drops the given JUnit file into src/test/, and hits Run. A test that passes
under grade.py can still be broken for the student -- e.g. one that reads its
own source with a path relative to the project root.

This rebuilds each student's project from their jar, placing every file by the
package it DECLARES (exports vary too much to trust their directory layout -
see dest_for), copies tests/*.java in the same way, compiles, and runs each test
class with the working directory set to the project root, the way an IDE does.

A student who scored n under grade.py should score n here. A difference means
the test behaves differently in the two environments, which is a bug in the
test rather than in the student's work.

Read-only with respect to the repo: reads grading/tests and grading/submissions,
writes only under the work directory it is given.
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grading", required=True, help="the grading/ directory")
    ap.add_argument("--work", required=True, help="scratch directory to build projects in")
    ap.add_argument("students", nargs="+", help="student id(s), or ALL")
    args = ap.parse_args()

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


if __name__ == "__main__":
    main()
