#!/usr/bin/env python3
"""Auto-Grader for Data Structures (Java + JUnit).

Compiles each student submission together with the week's fixed JUnit tests,
runs each official test class in its own isolated JVM invocation via the
JUnit Platform Console Launcher, and writes one row per student to a CSV:
student_id, compiled, tests_passed, tests_total, score, max_score,
uncapped_score, score_cap, passed_tests, failed_tests, failure_details, notes.
Score is 1 point per passed test by default, or a weighted sum if
tests/rubric.json is present. failure_details carries the JUnit assertion
message for each failed test (e.g. "expected: <0> but was: <-1>"), so a
failure can be understood straight from the CSV instead of re-reading the
test's source. A submission that fails to compile has its
extracted+flattened build directory preserved
under results/failed_builds/<student_id>__<n>/ for manual review,
regardless of --keep-build. If tests/structure.json is present
({"required_classes": [...]}), a submission missing one of those classes as
BOTH .java and .class is rejected before compiling ("STRUCTURE ERROR" in
notes), the same way a compile error is - extra classes beyond what's
required are never flagged. A submission that isn't a packaged .zip/.jar at
all - a bare loose .java file, or a folder of them (e.g. an LMS bulk
download bundling separately-uploaded files together) - is likewise a hard
"STRUCTURE ERROR", regardless of whether the loose source would otherwise
compile and pass; see the Submission.not_an_archive field.

A submission missing .java source for a class this week's tests actually
need (structure.json's required_classes, unioned with names automatically
inferred from what tests/*.java itself imports/instantiates - see
collect_required_class_names, so this works even without structure.json) is
still graded from that class's own precompiled .class if one is found
elsewhere in the submission (a runnable-jar export that dropped source is
the common case) - capped at 50% of max_score, since there's no source to
verify. A found .class whose own compiled package doesn't match what the
official tests naively expect (e.g. baked as `package main.java;` when the
tests need the class unqualified) still gets a genuine attempt via an
import-adjusted copy of just the official test - never the real
tests/*.java - falling back to a clear "can't be used" note only if that
attempt itself doesn't compile; see compile_with_class_fallback and
partition_fallback_matches. A submission that only yielded gradable content
after digging past a plain unzip (a .zip wrapping a nested jar, say) is
capped at 90%,
independent of whether source was ultimately found. Both apply together
multiplicatively (0.5 x 0.9 = 45%) when both are true. uncapped_score always
records the pre-cap result; score_cap shows the cap percentage applied
("" when none); notes explains why ("SCORE CAPPED AT n%: ...").

If tests/manual_review.json is present ({"checks": [{"pattern": <regex>,
"reason": <str>, "exclude_classes": [...], "auto_reject": <bool>}, ...]}),
every student .java file is scanned against each pattern and a match appends
a "MANUAL REVIEW: ..." note - a flag for a TA to read, and, only for a check
with "auto_reject": true, also a hard 0% score cap (same mechanism as the
50%/90% caps above - uncapped_score still records what it would have been).
"auto_reject" defaults to false, so an older manual_review.json with no such
checks behaves exactly as before: notes only. For behavior JUnit's tests
structurally can't tell apart from the real thing, e.g. a submission that
fakes polymorphic dispatch with an instanceof chain instead of overriding;
see load_manual_review_checks / run_manual_review_checks.
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
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

BUILD_ROOT = Path("build_tmp")
OUTPUT_TRUNCATE_CHARS = 2000
OUTPUT_TRUNCATE_LINES = 40
RMTREE_RETRY_ATTEMPTS = 5
RMTREE_RETRY_DELAY_SECONDS = 2.0

# The exact header line the JVM prints (to stderr, then repeats atop
# hs_err_pid<pid>.log) when a javac subprocess dies from native-memory
# exhaustion on the machine running grade.py itself - never something the
# student's code caused. Matched verbatim so this can only ever fire on
# that specific crash, not on a compile error that happens to mention
# "memory" in a student's own message/identifier.
JVM_NATIVE_OOM_SIGNATURE = "There is insufficient memory for the Java Runtime Environment to continue"

PUBLIC_TYPE_RE = re.compile(
    r"public\s+(?:final\s+|abstract\s+)?(?:class|interface|enum|record)\s+(\w+)"
)
METHOD_NAME_RE = re.compile(r"^\w+")
PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
STUDENT_ID_RE = re.compile(r"^\d+")
JUNIT_IMPORT_RE = re.compile(r"^\s*import\s+org\.junit\b", re.MULTILINE)


def rmtree_with_retry(path: Path) -> None:
    """shutil.rmtree, but tolerant of a file still being transiently locked
    by something outside our control - a cloud-sync client (OneDrive,
    Dropbox) hashing/uploading a file the instant after it's created is the
    common case if this project lives inside a synced folder, but Windows
    Search indexing or antivirus real-time scanning can do the same thing.
    That lock is normally released within a second or two on its own, so
    retrying briefly turns a hard crash into (at worst) a few seconds of
    waiting - only a lock that's still held after every retry becomes a
    real, reported error."""
    last_exc: OSError | None = None
    for attempt in range(RMTREE_RETRY_ATTEMPTS):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            last_exc = exc
            if attempt < RMTREE_RETRY_ATTEMPTS - 1:
                time.sleep(RMTREE_RETRY_DELAY_SECONDS)
    sys.exit(
        f"ERROR: could not remove {path} after {RMTREE_RETRY_ATTEMPTS} attempts ({last_exc}). "
        f"Something still has a file inside it open - close any editor/terminal browsing that "
        f"folder, let antivirus/cloud-sync settle, and try again."
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
IMPORT_CLASS_RE = re.compile(r"^\s*import\s+(?!static\s+)([\w.]+)\.(\w+)\s*;", re.MULTILINE)
NEW_EXPR_RE = re.compile(r"\bnew\s+([A-Z]\w*)\s*[(<]")

NON_STUDENT_IMPORT_PREFIXES = (
    "java.", "javax.", "org.junit", "org.opentest4j", "org.apiguardian",
    "org.hamcrest", "org.mockito", "junit.",
)

COMMON_JDK_TYPE_NAMES = {
    "String", "Integer", "Double", "Float", "Long", "Short", "Byte", "Character",
    "Boolean", "Object", "ArrayList", "LinkedList", "HashMap", "TreeMap", "HashSet",
    "TreeSet", "List", "Map", "Set", "Queue", "Deque", "Stack", "Vector", "Scanner",
    "Random", "StringBuilder", "StringBuffer", "Exception", "RuntimeException",
    "IllegalArgumentException", "IllegalStateException", "NullPointerException",
    "IndexOutOfBoundsException", "Thread", "Optional", "Comparator", "Iterator",
    "Arrays", "Collections", "Math", "System",
}


def collect_required_class_names(test_files: list[Path]) -> set[str]:
    """Best-effort inference of which student classes this week's official
    tests actually exercise - lets the .class-fallback path (see
    grade_student/find_class_fallback_files) work every week without
    needing tests/structure.json set up, since that file is optional and
    the two aren't the same list in general (structure.json is about
    catching a wrong project layout; this is about knowing which classes
    a passing-but-source-less submission is even allowed to substitute
    bytecode for).

    Two signals, unioned: an explicit `import pkg.ClassName;` (the common
    case - e.g. MisaShopTest2.java imports application.MisaShop,
    logic.Item, logic.Order, logic.OrderItem explicitly, which is exactly
    this week's required_classes) naming the exact class; and, for weeks
    whose tests assume the unnamed package instead (no import at all - see
    strip_package_declaration), a `new ClassName(...)` constructor call,
    which a test exercising a student class practically always has at
    least one of. A name matching a common JDK/collections type (ArrayList,
    String, ...) is dropped from the second signal to cut down noise; a
    false positive that slips through anyway is harmless - it only ever
    matters if a like-named .class also turns up inside the student's own
    submission, which a JDK class never does."""
    names: set[str] = set()
    for tf in test_files:
        text = tf.read_text(encoding="utf-8", errors="ignore")
        for pkg, cls in IMPORT_CLASS_RE.findall(text):
            if pkg.startswith(NON_STUDENT_IMPORT_PREFIXES):
                continue
            names.add(cls)
        for cls in NEW_EXPR_RE.findall(text):
            if cls in COMMON_JDK_TYPE_NAMES:
                continue
            names.add(cls)
    return names


def infer_unnamed_package_classes(test_files: list[Path]) -> set[str]:
    """Subset of collect_required_class_names' inferred names that the
    official tests can ONLY ever see in the unnamed/default package - i.e.
    named exclusively via a bare `new ClassName(...)` and never via a
    qualified `import pkg.ClassName;` for that same class anywhere in this
    week's tests. Used by partition_fallback_matches to know which candidate
    a found .class needs an import-adjusted test copy to reach: a class the
    tests reference unqualified needs to land at the classpath root or be
    imported by fully-qualified name, one or the other, so a candidate left
    nested under some directory after resolve_class_fallback_dest's own
    trimming needs the latter (see compile_with_class_fallback). A class
    instead reached via a qualified import is left alone here -
    resolve_class_fallback_dest already knows how to place (or correctly
    reject) that case on its own."""
    qualified: set[str] = set()
    unqualified: set[str] = set()
    for tf in test_files:
        text = tf.read_text(encoding="utf-8", errors="ignore")
        for pkg, cls in IMPORT_CLASS_RE.findall(text):
            if pkg.startswith(NON_STUDENT_IMPORT_PREFIXES):
                continue
            qualified.add(cls)
        for cls in NEW_EXPR_RE.findall(text):
            if cls in COMMON_JDK_TYPE_NAMES:
                continue
            unqualified.add(cls)
    return unqualified - qualified


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


IMPORT_LINE_RE = re.compile(r"^[ \t]*import\s+(?:static\s+)?([\w.*]+)\s*;[ \t]*\r?\n?", re.MULTILINE)


def add_imports(text: str, imports: list[str]) -> str:
    """Inserts `import <fqcn>;` for each entry in imports right after the
    last existing top-level import statement in text (or at the very top if
    there are none), skipping any entry text already imports verbatim so
    calling this twice is harmless. Used only by compile_with_class_fallback
    to build a throwaway, in-memory copy of an official test file for one
    compile attempt - see that function for why this never touches the
    actual file on disk that test_files points at."""
    matches = list(IMPORT_LINE_RE.finditer(text))
    existing = {m.group(1) for m in matches}
    to_add = [f"import {fqcn};\n" for fqcn in imports if fqcn not in existing]
    if not to_add:
        return text
    insert_at = matches[-1].end() if matches else 0
    return text[:insert_at] + "".join(to_add) + text[insert_at:]


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


SKIP_DIR_NAMES = {"out", "target", "bin", "build", ".git", ".idea", ".vscode", ".settings"}
# find_class_files' own, smaller exclusion list - out/target/bin/build are exactly
# where a real IDE/build tool puts its .class output (Eclipse: bin/, Maven: target/,
# Gradle: build/, IntelliJ: out/), which is precisely what the .class-fallback feature
# (find_class_fallback_files) needs to be able to see. Reusing SKIP_DIR_NAMES here would
# make that feature blind to the most common real-world case it exists for. Only VCS/IDE
# metadata dirs are excluded - never a build-output dir, never a .java-source concern.
CLASS_SEARCH_SKIP_DIR_NAMES = {".git", ".idea", ".vscode", ".settings"}


def find_java_files(root: Path) -> list[Path]:
    """Like root.rglob("*.java"), but never descends into a directory whose
    name is a known build-output or IDE-metadata folder (out, target, bin,
    build, .git, .idea, .vscode, .settings). A build-artifact folder's
    .class files are already invisible to a *.java glob, but the walk
    itself doesn't otherwise know to stay out of one - and a student's
    export sometimes bundles one in (an IntelliJ out/, a stray .git),
    along with whatever else. Nothing under one of these was ever part of
    what the student actually wrote, so it's excluded before anything else
    even sees it - not just ignored by extension."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for name in filenames:
            if name.endswith(".java"):
                found.append(Path(dirpath) / name)
    return sorted(found)


def find_class_files(root: Path) -> list[Path]:
    """Like find_java_files, but for *.class - used to locate a student's
    own precompiled classes (see find_class_fallback_files) when their
    .java source for a required class is missing. Deliberately NOT the same
    exclusion list as find_java_files (see CLASS_SEARCH_SKIP_DIR_NAMES) -
    out/, target/, bin/, build/ are exactly where a real compile actually
    puts its output, so excluding them here would make the .class-fallback
    feature blind to the common case it exists for. A discovered
    submission's extracted tree routinely also contains hundreds of
    unrelated *.class files from a bundled JUnit library (a "runnable jar
    with dependencies" export pulls in the whole org.junit.* tree) - this
    just enumerates everything *.class, the caller (find_class_fallback_files)
    is what filters that down to names that actually matter."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in CLASS_SEARCH_SKIP_DIR_NAMES]
        for name in filenames:
            if name.endswith(".class"):
                found.append(Path(dirpath) / name)
    return sorted(found)


def find_class_fallback_files(class_search_root: Path, class_names: set[str]) -> dict[str, list[Path]]:
    """For each simple class name in class_names, find its ClassName.class
    file (plus any ClassName$Inner.class sibling - same outer class, still
    needed at runtime) somewhere under class_search_root. Returns
    {class_name: [ClassName.class, ...]}, only for names actually found -
    the caller checks which requested names are still missing via the
    returned dict's keys. The path each .class file is found at (relative
    to class_search_root) is its package for Java's purposes, but may sit
    under an extra non-package wrapper directory (see
    resolve_class_fallback_dest for how the caller corrects for that)."""
    if not class_names:
        return {}
    result: dict[str, list[Path]] = {}
    for class_file in find_class_files(class_search_root):
        simple = class_file.stem.split("$", 1)[0]
        if simple in class_names:
            result.setdefault(simple, []).append(class_file)
    return result


def resolve_class_fallback_dest(rel_path: Path, referenced_packages: set[str]) -> Path:
    """A fallback .class file's path relative to class_search_root is
    normally its package for Java's purposes, but may sit under an extra
    directory level that's a pure extraction/packaging artifact rather than
    part of the class's actual compiled package - e.g. a student zipping
    their whole Eclipse project, so the true classpath root is
    ProjectName/bin/logic/Item.class. If rel_path's directory matches one of
    referenced_packages as a dotted suffix, this trims it down to just that
    package, so a class genuinely compiled as `package logic;` is found on
    the classpath at `logic/Item.class` regardless of what sat above it in
    the submission's own layout.

    This does NOT rewrite the class - unlike source text (see
    strip_package_declaration's Q1_toStudent.application example), a
    compiled .class file's package is baked into its own bytecode (the
    this_class constant pool entry), not inferred from its file path. A
    class whose real compiled package genuinely includes the wrapper - e.g.
    one actually compiled as `package Q2_toStudent.logic;` - still ends up
    on the classpath at the trimmed location here, but javac's own
    classfile verification then rejects it ("class file contains wrong
    class") since the file's internal identity doesn't match. That's
    correct: if the official test's `import logic.Item;` wouldn't resolve
    against the student's own actual package on their own machine, it
    shouldn't resolve here either - this function can only undo an
    extraction-level accident, not a genuine packaging mistake.

    Returns rel_path unchanged when there's no wrapper to strip (the common
    case) or no referenced package to match against (the unnamed-package
    case)."""
    if not referenced_packages:
        return rel_path
    dotted_dir = ".".join(rel_path.parent.parts)
    canonical = max(
        (kp for kp in referenced_packages if dotted_dir == kp or dotted_dir.endswith("." + kp)),
        key=len,
        default=None,
    )
    if canonical is None:
        return rel_path
    return Path(*canonical.split(".")) / rel_path.name


def partition_fallback_matches(
    fallback_matches: dict[str, list[Path]],
    class_search_root: Path,
    test_files: list[Path],
) -> tuple[dict[str, list[Path]], dict[str, tuple[Path, str]]]:
    """Splits fallback_matches into (safe, needs_import).

    safe candidates resolve_class_fallback_dest already places correctly on
    its own - found directly at the unnamed-package root, or reached via a
    qualified import that its canonical-package trimming handles (or
    correctly rejects with "class file contains wrong class" at compile
    time, exactly as documented in resolve_class_fallback_dest) - unchanged
    from before this function existed.

    needs_import holds a class infer_unnamed_package_classes says the tests
    need UNQUALIFIED whose only candidate(s) are compiled under some other
    real named package instead (e.g. `main.java`, a common IDE default this
    exact cohort's SOURCE submissions already get forgiven for via
    strip_package_declaration - this is the .class-only equivalent): one
    representative (file, fully-qualified class name) per class name,
    worth a genuine attempt via add_imports + compile_with_class_fallback
    rather than an outright rejection, since the class file itself may be
    entirely correct and just sitting somewhere the test wasn't written to
    import from. Never modifies resolve_class_fallback_dest's own trimming
    logic - a class whose real package merely SHARES a prefix with what's
    required is still left to that function and to compile-time
    verification, not decided here."""
    referenced_packages = collect_referenced_packages(test_files)
    unnamed_required = infer_unnamed_package_classes(test_files)
    safe: dict[str, list[Path]] = {}
    needs_import: dict[str, tuple[Path, str]] = {}
    for class_name, files in fallback_matches.items():
        safe_files: list[Path] = []
        candidate: tuple[Path, str] | None = None
        for f in files:
            rel = f.relative_to(class_search_root)
            dest_rel = resolve_class_fallback_dest(rel, referenced_packages)
            if class_name in unnamed_required and dest_rel.parent != Path("."):
                if candidate is None:
                    package = ".".join(dest_rel.parent.parts)
                    candidate = (f, f"{package}.{class_name}")
                continue
            safe_files.append(f)
        if safe_files:
            safe[class_name] = safe_files
        elif candidate is not None:
            needs_import[class_name] = candidate
    return safe, needs_import


def find_nested_archives(root: Path) -> list[Path]:
    """.zip/.jar files sitting inside an already-extracted submission - e.g.
    a student who zipped up their built .jar instead of submitting it
    directly, so the real project is one archive level deeper than a
    normal submission. Only ever consulted as a fallback (see
    discover_submissions) when the straightforward extraction turns up
    zero .java files: a student's own bundled JUnit library jars
    (lib/junit-jupiter-*.jar) are also .jar files sitting in the tree, and
    blindly extracting those would be wasted work - a library ships only
    .class files, never a student's own source, so it would never change
    the answer for a submission that already has real .java files."""
    return sorted(root.rglob("*.zip")) + sorted(root.rglob("*.jar"))


@dataclass
class Submission:
    student_id: str
    java_files: list[Path]
    notes: list[str]
    # Where find_class_fallback_files should look for the student's own
    # precompiled .class files when .java source for a required class is
    # missing (see grade_student) - None when there's nowhere meaningful to
    # search (a single loose .java file submission).
    class_search_root: Path | None = None
    # True only when the top-level submission was itself a .zip (not a
    # .jar - a jar IS the artifact, nothing was "unzipped" to reach it) and
    # its own first-level extraction alone found no .java - i.e. grading it
    # required going beyond a plain unzip, whether that eventually turned
    # up real source (in a nested jar) or only compiled classes. This is
    # the "improper packaging" score-cap signal in grade_student,
    # independent of whether source was ultimately found.
    zip_needed_deeper_extraction: bool = False
    # True when the raw top-level submissions/ entry was a bare .java file or
    # a directory of loose files, rather than a .zip/.jar - i.e. the student
    # never packaged their submission at all (a real case: an LMS bulk
    # download bundling two individually-uploaded files, e.g.
    # Bot-<id>-<ts>.java and Part-<id>-<ts>.java, into a folder). Graded as a
    # hard STRUCTURE ERROR in grade_student regardless of whether the loose
    # source would otherwise compile and pass - submitting a packaged archive
    # is part of the assignment's required format, not just a convenience for
    # this grader. Deliberately does NOT cover a loose .class file - that's
    # find_class_fallback_files' own, separate, intentional path for a
    # runnable-jar export that dropped its source, not a format violation.
    not_an_archive: bool = False


def discover_submissions(submissions_dir: Path, extract_root: Path) -> list[Submission]:
    """A submission may be:
    - a folder (e.g. an LMS bulk download bundling multiple individually-uploaded loose
      files together - any nesting under it is scanned via find_java_files, which both
      filters to *.java and skips known build/IDE folders) - graded as a hard
      STRUCTURE ERROR in grade_student (not_an_archive=True): the assignment requires a
      packaged .zip/.jar, so this is a format violation regardless of what's inside
    - a single loose .java file - same STRUCTURE ERROR treatment as a folder, above
    - a single loose .class file (already-compiled, no source at all) - copied into its
      own extract_root/<n>/ so find_class_fallback_files has an isolated place to look
      for it, never the shared submissions_dir (which would risk matching another
      student's same-named class)
    - a .zip or .jar file (a JAR is just a ZIP file with a manifest, so the same extraction
      works for both - e.g. an Eclipse project exported as a zip, or exported as a runnable
      JAR with sources included) - extracted into extract_root/<n>/ (a plain sequential
      counter, NOT the student_id: real submission filenames can be arbitrarily long -
      LMS downloads, browser dedup suffixes, etc. - and combined with this repo's own path
      depth plus a jar's internal package structure, that reliably blows past Windows'
      260-character path limit during extraction) and then scanned like a folder submission.
    """
    results: list[Submission] = []
    entries = sorted(submissions_dir.iterdir())
    total = len(entries)
    for idx, entry in enumerate(entries):
        print(f"  [{idx + 1}/{total}] unpacking {entry.name} ...", flush=True)
        if entry.is_dir():
            java_files = find_java_files(entry)
            results.append(Submission(
                entry.name, java_files, [], class_search_root=entry, not_an_archive=True,
            ))
        elif entry.is_file() and entry.suffix == ".java":
            results.append(Submission(entry.stem, [entry], [], not_an_archive=True))
        elif entry.is_file() and entry.suffix == ".class":
            student_id = entry.stem
            class_dir = extract_root / str(idx)
            if class_dir.exists():
                rmtree_with_retry(class_dir)
            class_dir.mkdir(parents=True)
            shutil.copy2(entry, class_dir / entry.name)
            results.append(Submission(student_id, [], [], class_search_root=class_dir))
        elif entry.is_file() and entry.suffix in (".zip", ".jar"):
            student_id = entry.stem
            extract_dir = extract_root / str(idx)
            if extract_dir.exists():
                rmtree_with_retry(extract_dir)
            extract_dir.mkdir(parents=True)
            try:
                with zipfile.ZipFile(entry) as zf:
                    zf.extractall(extract_dir)
            except zipfile.BadZipFile:
                results.append(Submission(student_id, [], [f"could not open {entry.name}: not a valid zip/jar file"]))
                continue
            except OSError as exc:
                # e.g. Windows path-length limit blown by a long filename/deeply
                # nested entry inside the archive - must not crash the whole batch.
                results.append(Submission(student_id, [], [f"could not extract {entry.name}: {exc}"]))
                continue
            java_files = find_java_files(extract_dir)
            notes: list[str] = []
            zip_needed_deeper_extraction = entry.suffix == ".zip" and not java_files
            if not java_files:
                for nested in find_nested_archives(extract_dir):
                    try:
                        with zipfile.ZipFile(nested) as nzf:
                            nzf.extractall(extract_dir)
                    except (zipfile.BadZipFile, OSError):
                        continue
                    java_files = find_java_files(extract_dir)
                    if java_files:
                        notes.append(
                            f"{entry.name} contained no .java files directly - found them inside "
                            f"a nested archive ({nested.relative_to(extract_dir)}) and extracted that too"
                        )
                        break
            if not java_files:
                notes.append(f"{entry.name} extracted OK but contained no .java files")
            results.append(Submission(
                student_id, java_files, notes,
                class_search_root=extract_dir,
                zip_needed_deeper_extraction=zip_needed_deeper_extraction,
            ))
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
    Works on source text alone, so it can run before compiling to decide
    what to exclude on a fallback compile - see
    compile_submission_with_fallback."""
    extra: list[Path] = []
    for java_file in sorted(build_dir.glob("*.java")):
        if java_file.name in official_names:
            continue
        text = java_file.read_text(encoding="utf-8", errors="ignore")
        if JUNIT_IMPORT_RE.search(text):
            extra.append(java_file)
    return extra


def prepare_build_dir(
    build_key: str, student_files: list[Path], test_files: list[Path], build_root: Path
) -> tuple[Path, list[str]]:
    notes: list[str] = []
    build_dir = build_root / build_key
    if build_dir.exists():
        rmtree_with_retry(build_dir)
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

    # classes_dir is on the compile classpath (not just the -d output target) so that
    # any precompiled .class files grade_student seeded into it beforehand (see
    # find_class_fallback_files - a required class missing .java source, substituted
    # with the student's own bytecode) resolve as a dependency for whatever real .java
    # files ARE being compiled here. A no-op for the common case where classes_dir
    # starts out empty.
    cmd = [
        "javac",
        "-cp", f"{junit_jar}{os.pathsep}{classes_dir}",
        "-d", str(classes_dir),
        "-encoding", "UTF-8",
        *[str(f) for f in java_files],
    ]
    result = run_with_hard_timeout(cmd, timeout)
    if result.timed_out:
        return CompileResult(False, classes_dir, "javac timed out")

    if result.returncode != 0:
        output = (result.stdout + result.stderr).replace(str(build_dir) + "\\", "").replace(str(build_dir) + "/", "")
        if JVM_NATIVE_OOM_SIGNATURE in output:
            return CompileResult(
                False, classes_dir,
                "javac ran out of memory on this machine, not a code issue - "
                "close other programs and rerun grade.py for this submission",
            )
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


def load_structure_baseline(tests_dir: Path) -> list[str] | None:
    """Optional tests/structure.json: {"required_classes": ["Station", ...]}.
    When present, every submission must define exactly these top-level
    classes - and no unexpected extras, see check_structure_baseline -
    before it's even compiled. Absent by default so weeks without one
    behave exactly as before. Malformed (not just absent) fails loudly at
    startup rather than as a silent per-student side effect, the same way
    a misconfigured lib/ directory already does (see find_junit_jar) - a
    broken config for the whole run should never be discovered one student
    at a time."""
    structure_path = tests_dir / "structure.json"
    if not structure_path.exists():
        return None
    with open(structure_path, encoding="utf-8") as f:
        data = json.load(f)
    required = data.get("required_classes")
    if not isinstance(required, list) or not all(isinstance(c, str) for c in required):
        sys.exit(
            f'ERROR: {structure_path} must contain a "required_classes" list of class '
            f'name strings, e.g. {{"required_classes": ["Station", "Ticket"]}}.'
        )
    return required


def load_manual_review_checks(tests_dir: Path) -> list[dict] | None:
    """Optional tests/manual_review.json: {"checks": [{"pattern": <regex>,
    "reason": <str>, "exclude_classes": [<class name>, ...],
    "auto_reject": <bool>}, ...]}. When present, run_manual_review_checks
    scans every student .java file for each pattern and appends a
    "MANUAL REVIEW: ..." note to notes when one matches - by default that's
    purely a flag for a TA to read, never touching compiled/tests_passed/
    score. For things JUnit's behavioral tests structurally can't catch -
    e.g. a submission that reimplements polymorphic dispatch with an
    instanceof chain instead of overriding passes the exact same tests
    either way, so the tests alone can't tell the two apart. Absent by
    default so weeks without one behave exactly as before. Malformed (not
    just absent) fails loudly at startup, the same way tests/structure.json
    does - a broken config for the whole run should never be discovered one
    student at a time. "exclude_classes" is optional per check (defaults to
    none exempted) - e.g. a week whose game rules legitimately require one
    specific class to use instanceof. "auto_reject" is also optional per
    check (defaults to false) - when true, a match doesn't just leave a
    note, it also forces a hard 0% score cap in grade_student (see the
    score-cap section there), for a check whose marking guide says a match
    should be rejected outright rather than just flagged for a human to
    look at."""
    checks_path = tests_dir / "manual_review.json"
    if not checks_path.exists():
        return None
    with open(checks_path, encoding="utf-8") as f:
        data = json.load(f)
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        sys.exit(
            f'ERROR: {checks_path} must contain a non-empty "checks" list, e.g. '
            f'{{"checks": [{{"pattern": "instanceof", "reason": "...", '
            f'"exclude_classes": ["Boss"]}}]}}.'
        )
    for check in checks:
        if (
            not isinstance(check, dict)
            or not isinstance(check.get("pattern"), str)
            or not isinstance(check.get("reason"), str)
        ):
            sys.exit(
                f'ERROR: every entry in {checks_path}\'s "checks" list needs a string '
                f'"pattern" and a string "reason".'
            )
        exclude = check.get("exclude_classes", [])
        if not isinstance(exclude, list) or not all(isinstance(c, str) for c in exclude):
            sys.exit(
                f'ERROR: "exclude_classes" in {checks_path} must be a list of class '
                f"name strings."
            )
        if "auto_reject" in check and not isinstance(check["auto_reject"], bool):
            sys.exit(f'ERROR: "auto_reject" in {checks_path} must be a boolean.')
        try:
            re.compile(check["pattern"])
        except re.error as exc:
            sys.exit(
                f'ERROR: invalid regex "pattern" in {checks_path}: '
                f"{check['pattern']!r} ({exc})"
            )
    return checks


def check_structure_baseline(
    build_dir: Path,
    official_names: set[str],
    required_classes: list[str],
    covered_by_class_fallback: set[str] = frozenset(),
) -> list[str]:
    """Compares the submission's flattened, normalized file set (see
    prepare_build_dir - Main.java already excluded, packages already
    resolved to their canonical names) against tests/structure.json's
    required_classes. Meant to run BEFORE compiling, so a submission
    missing a required class gets one specific, readable reason instead of
    a wall of downstream "cannot find symbol" errors from every file that
    referenced it.

    Only checks for MISSING required classes, not extra ones: the mental
    model is "if we swapped in the official test file on the student's own
    machine, would it work" - an extra class sitting unused alongside the
    required ones doesn't break that, so it isn't a violation. (A name
    COLLISION with an official test file is already handled earlier, in
    prepare_build_dir.) Returns a list of violation messages, empty if
    every required class is present.

    covered_by_class_fallback (see find_class_fallback_files in
    grade_student) is also never a violation: a required class missing its
    .java but with a matching .class discovered elsewhere in the submission
    is still gradable, just from bytecode instead of source - that's a
    score cap applied later in grade_student, not a hard structure
    rejection here. Default empty set - callers that never pass it keep the
    exact prior behavior."""
    present_classes = {
        f.stem for f in build_dir.glob("*.java") if f.name not in official_names
    }
    return [
        f"missing required class {name} (expected {name}.java)"
        for name in required_classes
        if name not in present_classes and name not in covered_by_class_fallback
    ]


def run_manual_review_checks(
    build_dir: Path, official_names: set[str], checks: list[dict]
) -> tuple[list[str], list[str]]:
    """Scans every student .java file already flattened into build_dir (see
    prepare_build_dir - Main.java already excluded, packages already
    resolved to their canonical names) against each tests/manual_review.json
    check, skipping a file for a given check when that file's own resolved
    class name is in the check's "exclude_classes". A match produces one
    note PER CHECK (not per file), listing every matching file and the
    1-based line number of its first match, e.g. "MANUAL REVIEW: <reason> -
    found in Unit.java (line 14), Warrior.java (line 22)".

    Returns (notes, reject_reasons): notes is purely additive - the caller
    folds it into notes alongside everything else, and it never by itself
    touches compiled/tests_passed/score. reject_reasons lists the "reason"
    (plus the same "found in ..." detail) of every check that both matched
    AND has "auto_reject": true - the caller uses a non-empty list to force
    a 0% score cap (see the score-cap section in grade_student), same as
    the note but with real scoring consequence."""
    student_files = sorted(
        f for f in build_dir.glob("*.java") if f.name not in official_names
    )
    notes: list[str] = []
    reject_reasons: list[str] = []
    for check in checks:
        pattern = re.compile(check["pattern"])
        excluded = set(check.get("exclude_classes", []))
        hits: list[str] = []
        for f in student_files:
            if f.stem in excluded:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
            match = pattern.search(text)
            if match:
                line_no = text.count("\n", 0, match.start()) + 1
                hits.append(f"{f.name} (line {line_no})")
        if hits:
            detail = f"{check['reason']} - found in {', '.join(hits)}"
            notes.append(f"MANUAL REVIEW: {detail}")
            if check.get("auto_reject"):
                reject_reasons.append(detail)
    return notes, reject_reasons


def compile_with_class_fallback(
    build_dir: Path,
    junit_jar: Path,
    timeout: int,
    official_names: set[str],
    test_files: list[Path],
    needs_import: dict[str, tuple[Path, str]],
) -> tuple[CompileResult, list[str], list[str]]:
    """Official test .java files are already sitting in build_dir as verbatim
    copies (see prepare_build_dir) when this runs. needs_import (see
    partition_fallback_matches) names classes whose only fallback .class is
    compiled under a real package the official tests weren't written to
    import from - e.g. `main.java`, baked permanently into that .class's own
    bytecode (see resolve_class_fallback_dest's docstring for why that can
    never be changed by moving the file). The class itself may be entirely
    correct; only the test's own import is missing.

    For each such class, every official test file that references it via a
    bare `new ClassName(...)` gets add_imports applied to an IN-MEMORY copy
    of its text, OVERWRITING the copy already sitting in build_dir - never
    the file test_files itself points at, which this function never opens
    for writing. A single compile is then attempted with those adjustments
    in place. If it succeeds, that's the final answer: the class genuinely
    works once the test can see it, so it's graded for real against the
    student's real, unmodified bytecode - the caller still applies the
    normal 50%-cap policy on top, since there's still no .java source to
    verify. If it fails, every adjusted file is restored to its exact
    original content (byte-for-byte, so a second failed guess can never
    leave build_dir in a worse state than before this function ran) and one
    plain compile is attempted with no adjustment at all, matching exactly
    what would have happened had needs_import been empty from the start.

    Returns (compile_result, used_names, notes): used_names is which
    needs_import class names the successful attempt actually resolved
    (list(needs_import) on success, empty list if nothing was attempted or
    the attempt failed and was reverted) - the caller uses an empty
    used_names to know it must re-derive structure/compile-error reporting
    without treating needs_import as covered."""
    if not needs_import:
        result, notes = compile_submission_with_fallback(build_dir, junit_jar, timeout, official_names)
        return result, [], notes

    imports_by_test_file: dict[Path, list[str]] = {}
    for class_name, (_file, fqcn) in needs_import.items():
        for tf in test_files:
            text = tf.read_text(encoding="utf-8", errors="ignore")
            if re.search(rf"\bnew\s+{re.escape(class_name)}\s*[(<]", text):
                imports_by_test_file.setdefault(build_dir / tf.name, []).append(fqcn)

    originals: dict[Path, str] = {}
    for dest, fqcns in imports_by_test_file.items():
        original_text = dest.read_text(encoding="utf-8", errors="ignore")
        originals[dest] = original_text
        dest.write_text(add_imports(original_text, fqcns), encoding="utf-8")

    result, notes = compile_submission_with_fallback(build_dir, junit_jar, timeout, official_names)
    if result.success:
        used_names = sorted(needs_import)
        notes = notes + [
            f"found precompiled {name}.class elsewhere in the submission, compiled "
            f"under a different package than the official tests expect - adjusted "
            f"the official test's import to reach it directly (no .java source to "
            f"verify, so still capped)"
            for name in used_names
        ]
        return result, used_names, notes

    for dest, original_text in originals.items():
        dest.write_text(original_text, encoding="utf-8")
    result, notes = compile_submission_with_fallback(build_dir, junit_jar, timeout, official_names)
    return result, [], notes


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
    required_classes: list[str] | None,
    failed_build_root: Path | None = None,
    class_search_root: Path | None = None,
    zip_needed_deeper_extraction: bool = False,
    class_fallback_candidates: set[str] | None = None,
    not_an_archive: bool = False,
    manual_review_checks: list[dict] | None = None,
) -> dict:
    row = {
        "student_id": student_id,
        "compiled": "no",
        "tests_passed": 0,
        "tests_total": 0,
        "score": 0,
        "max_score": 0,
        "uncapped_score": 0,
        "score_cap": "",
        "passed_tests": "",
        "failed_tests": "",
        "failure_details": "",
        "notes": "",
    }
    build_dir = None
    try:
        if not student_files and class_search_root is None:
            row["notes"] = "; ".join(discovery_notes + ["no .java source files found"]).strip("; ")
            return row

        build_dir, prep_notes = prepare_build_dir(build_key, student_files, test_files, build_root)
        prep_notes = discovery_notes + prep_notes

        official_names = {f.name for f in test_files}

        manual_review_reject_reasons: list[str] = []
        if manual_review_checks:
            review_notes, manual_review_reject_reasons = run_manual_review_checks(
                build_dir, official_names, manual_review_checks
            )
            prep_notes = prep_notes + review_notes

        if not_an_archive:
            # Hard fail before ever attempting to compile, regardless of whether the
            # loose source is otherwise correct - see the Submission.not_an_archive
            # field for why this is treated as a format violation, not a convenience.
            row["notes"] = "; ".join(
                prep_notes + [
                    "STRUCTURE ERROR: submitted as bare .java source, not a packaged "
                    ".zip/.jar - the assignment requires an archive submission, "
                    "regardless of whether the source itself would compile"
                ]
            ).strip("; ")
            return row

        # Which of this week's required classes (structure.json's, unioned with
        # collect_required_class_names' inference from the official tests - see
        # main()) are missing .java source, and do we have that class's own
        # precompiled .class sitting elsewhere in the submission (a fat/runnable-jar
        # export that dropped source, most commonly)? Computed BEFORE the
        # structure.json hard-fail check below so a class covered by a class
        # fallback is never rejected outright - only one missing BOTH forms still is.
        present_java_classes = {
            f.stem for f in build_dir.glob("*.java") if f.name not in official_names
        }
        missing_source = (class_fallback_candidates or set()) - present_java_classes
        fallback_matches: dict[str, list[Path]] = {}
        needs_import: dict[str, tuple[Path, str]] = {}
        if missing_source and class_search_root is not None:
            raw_matches = find_class_fallback_files(class_search_root, missing_source)
            if raw_matches:
                # Split into candidates resolve_class_fallback_dest already places
                # correctly (fallback_matches) and ones compiled under a real
                # package the official tests weren't written to import from
                # (needs_import) - see partition_fallback_matches. The latter
                # still get a genuine compile attempt below (see
                # compile_with_class_fallback) rather than being rejected
                # outright, since the class file itself may be entirely correct.
                fallback_matches, needs_import = partition_fallback_matches(
                    raw_matches, class_search_root, test_files
                )

        # Both fallback_matches and needs_import count as "something was found"
        # for the checks below - whether a needs_import candidate truly resolves
        # is only known once compile_with_class_fallback actually tries it.
        all_candidates: dict[str, list[Path]] = {k: list(v) for k, v in fallback_matches.items()}
        for name, (f, _fqcn) in needs_import.items():
            all_candidates.setdefault(name, []).append(f)

        if not student_files and not all_candidates:
            row["notes"] = "; ".join(prep_notes + ["no .java source files found"]).strip("; ")
            return row

        if required_classes is not None:
            violations = check_structure_baseline(
                build_dir, official_names, required_classes,
                covered_by_class_fallback=set(all_candidates),
            )
            if violations:
                row["notes"] = "; ".join(
                    prep_notes + [f"STRUCTURE ERROR: {v}" for v in violations]
                ).strip("; ")
                return row

        if all_candidates:
            # Seed every discovered .class file into classes/ BEFORE compiling, at
            # its own natural resolved location - compile_submission puts
            # classes_dir on javac's own -cp too, so any remaining .java that
            # references one of these classes as a dependency still resolves. A
            # needs_import candidate sitting here with nothing importing it yet
            # is harmless either way: compile_with_class_fallback decides next
            # whether an adjusted test import can actually reach it.
            referenced_packages = collect_referenced_packages(test_files)
            classes_dir = build_dir / "classes"
            classes_dir.mkdir(exist_ok=True)
            # Two distinct source .class files can resolve to the same classpath
            # destination - legitimately for a $Inner sibling (same dest dir, different
            # filename, no collision) but also, rarely, for two unrelated copies of the
            # same simple name found in different corners of the submission (e.g. a
            # stale duplicate build folder SKIP_DIR_NAMES doesn't happen to cover).
            # copy2 would silently let the later one win; note it instead so a TA
            # reviewing a surprising score knows to check --keep-build.
            collisions: list[str] = []
            for files in all_candidates.values():
                for f in files:
                    rel = resolve_class_fallback_dest(f.relative_to(class_search_root), referenced_packages)
                    dest = classes_dir / rel
                    if dest.exists():
                        collisions.append(
                            f"{rel} (kept {f.relative_to(class_search_root)}, "
                            f"discarded an earlier duplicate)"
                        )
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
            if fallback_matches:
                prep_notes = prep_notes + [
                    "found precompiled .class (no .java source) for required class(es): "
                    + ", ".join(sorted(fallback_matches))
                ]
            if collisions:
                prep_notes = prep_notes + [
                    "WARNING: multiple .class files resolved to the same classpath "
                    "location, only the last one found was used: " + "; ".join(collisions)
                ]

        compile_result, used_needs_import, compile_notes = compile_with_class_fallback(
            build_dir, junit_jar, timeout, official_names, test_files, needs_import
        )
        prep_notes = prep_notes + compile_notes

        if not compile_result.success:
            if needs_import and not used_needs_import:
                # The generous, import-adjusted attempt didn't pan out and has
                # already been fully reverted inside compile_with_class_fallback -
                # re-derive whether this is now a genuine STRUCTURE ERROR (the
                # class truly has no usable form at all) using only fallback_matches
                # as "covered", exactly as if needs_import had never been found.
                prep_notes = prep_notes + [
                    f"found precompiled {name}.class elsewhere in the submission, but "
                    f"using it (via an adjusted test import) still didn't compile, so "
                    f"it can't be used as a substitute"
                    for name in sorted(needs_import)
                ]
                if required_classes is not None:
                    violations = check_structure_baseline(
                        build_dir, official_names, required_classes,
                        covered_by_class_fallback=set(fallback_matches),
                    )
                    if violations:
                        row["notes"] = "; ".join(
                            prep_notes + [f"STRUCTURE ERROR: {v}" for v in violations]
                        ).strip("; ")
                        return row
            row["notes"] = "; ".join(prep_notes + [f"COMPILE ERROR: {compile_result.output}"]).strip("; ")
            return row

        if used_needs_import:
            fallback_matches = {**fallback_matches}
            for name in used_needs_import:
                fallback_matches[name] = [needs_import[name][0]]

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

        # Score cap policy: a submission graded from precompiled .class instead of
        # .java (fallback_matches non-empty - see above) is capped at 50% of
        # max_score, since there's no source to verify; a submission that only
        # yielded gradable content after digging past a plain unzip
        # (zip_needed_deeper_extraction - see discover_submissions) is capped at
        # 90%, for improper packaging independent of whether source was found;
        # a submission that tripped an "auto_reject": true manual_review.json
        # check (manual_review_reject_reasons - see run_manual_review_checks)
        # is capped at 0%, for a marking-guide rule that says a match should be
        # rejected outright, not merely flagged for a human to look at later.
        # All apply multiplicatively when more than one is true (0.5 x 0.9 =
        # 45%; anything x 0% = 0%). uncapped_score always records what the raw
        # result would have been, for audit/appeal purposes, even when no cap
        # ends up binding.
        cap = 1.0
        cap_reasons: list[str] = []
        if fallback_matches:
            cap *= 0.5
            cap_reasons.append(
                "used precompiled .class instead of .java source for required class(es): "
                + ", ".join(sorted(fallback_matches))
            )
        if zip_needed_deeper_extraction:
            cap *= 0.9
            cap_reasons.append(
                "submission required extracting a nested/deeper archive to find gradable content"
            )
        if manual_review_reject_reasons:
            cap *= 0.0
            cap_reasons.append(
                "manual review check(s) require rejection: "
                + "; ".join(manual_review_reject_reasons)
            )
        row["uncapped_score"] = row["score"]
        if cap < 1.0:
            row["score"] = min(row["score"], cap * row["max_score"])
            row["score_cap"] = f"{cap:.0%}"
            extra.append(f"SCORE CAPPED AT {cap:.0%}: " + "; ".join(cap_reasons))

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


def check_output_writable(path: Path) -> str | None:
    """Probes whether path can be written to, without touching its content -
    opened in 'a' (append) mode, which creates it if missing but never
    truncates an existing file, then immediately closed. Meant to be called
    for every output path BEFORE the (potentially many-minutes-long) grading
    run starts: an output CSV left open in Excel - which holds an exclusive
    lock on Windows - would otherwise only surface as a PermissionError on
    the final write_csv/write_scores_csv call, discarding a completed run's
    results. Returns None if writable, else a message describing why not."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        return f"{path} is not writable ({exc}) - likely open in another program (e.g. Excel); close it and try again"
    return None


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sort_rows(rows)
    fieldnames = [
        "student_id", "compiled", "tests_passed", "tests_total", "score", "max_score",
        "uncapped_score", "score_cap",
        "passed_tests", "failed_tests", "failure_details", "notes",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_sorted)


def bare_student_id(student_id: str) -> str:
    """Just the numeric ID, stripping any trailing tag a submission's own
    filename carried (e.g. "6638002421_w1_q1" -> "6638002421", from a
    <id>_w1_q1.jar submission). grades.csv keeps the full original ID for
    traceability back to the exact submitted file; the gradebook-upload
    CSV needs the bare ID to match LMS records. Falls back to the ID
    unchanged if it doesn't start with digits at all, rather than guessing."""
    match = STUDENT_ID_RE.match(student_id)
    return match.group(0) if match else student_id


# Short, human-readable label for each distinct score-cap reason grade_student can
# produce (see the score-cap section there) - matched by a substring of the reason
# text that's always present in that case, checked in this order so the first hit
# wins if a submission somehow triggered more than one. Used only for the console
# progress line; grades.csv's own "notes" column keeps the full detail regardless.
CAP_REASON_LABELS = [
    ("manual review check(s) require rejection", "rejected by manual review"),
    ("used precompiled .class instead of .java source", "failed to include source file"),
    ("submission required extracting a nested/deeper archive", "failed to submit as .jar"),
]


def short_cap_reason(notes: str) -> str:
    """Best-effort short label for the console (e.g. "rejected by manual review")
    derived from grades.csv's own full "SCORE CAPPED AT n%: <reason(s)>" notes
    segment - falls back to that segment verbatim if the reason doesn't match any
    known label (e.g. a future cap case this list hasn't been updated for yet)."""
    idx = notes.find("SCORE CAPPED AT")
    if idx == -1:
        return ""
    segment = notes[idx:]
    for marker, label in CAP_REASON_LABELS:
        if marker in segment:
            return label
    return segment


def write_scores_csv(rows: list[dict], out_path: Path) -> None:
    """Simple 2-column CSV (student_id, score), no header row - MyCourseVille's
    gradebook import reads this directly and doesn't expect one. student_id
    here is always the bare numeric ID (see bare_student_id), regardless of
    what a submission's own filename was tagged with."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sort_rows(rows)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in rows_sorted:
            writer.writerow([bare_student_id(row["student_id"]), row["score"]])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-grade Java submissions with JUnit 5.")
    parser.add_argument("--submissions", default="submissions")
    parser.add_argument("--tests", default="tests")
    parser.add_argument("--lib", default="lib")
    parser.add_argument("--out", default=str(Path("results") / "grades.csv"),
                         help="detailed CSV: student_id, compiled, tests_passed, tests_total, "
                              "score, max_score, uncapped_score, score_cap, passed_tests, "
                              "failed_tests, failure_details, notes")
    parser.add_argument("--scores-out", default=str(Path("results") / "mcvScore.csv"),
                         help="simple CSV, no header row: student_id, score (bare numeric ID) "
                              "- for MyCourseVille gradebook upload")
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
    for output_path in (out_path, scores_out_path):
        problem = check_output_writable(output_path)
        if problem is not None:
            sys.exit(f"ERROR: {problem}")

    junit_jar = find_junit_jar(lib_dir)
    test_files = discover_test_files(tests_dir)
    test_classes = [test_class_fqcn(tf) for tf in test_files]
    rubric = load_rubric(tests_dir)
    required_classes = load_structure_baseline(tests_dir)
    manual_review_checks = load_manual_review_checks(tests_dir)
    class_fallback_candidates = collect_required_class_names(test_files) | set(required_classes or [])

    if build_root.exists():
        rmtree_with_retry(build_root)
    build_root.mkdir(parents=True)
    extract_root = build_root / "_extracted"
    extract_root.mkdir(parents=True)

    if failed_build_root.exists():
        rmtree_with_retry(failed_build_root)
    failed_build_root.mkdir(parents=True)

    print(f"Auto-Grader for Data Structures - unpacking submissions from {submissions_dir} ...")
    submissions = discover_submissions(submissions_dir, extract_root)
    if not submissions:
        sys.exit(f"ERROR: no student submissions found in {submissions_dir}")

    id_counts = Counter(sub.student_id for sub in submissions)
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
    if required_classes is not None:
        print(f"  structure:   {tests_dir / 'structure.json'}  (required classes: {', '.join(required_classes)})")
    else:
        print("  structure:   none (tests/structure.json not found - no structure check)")
    if manual_review_checks is not None:
        reject_count = sum(1 for c in manual_review_checks if c.get("auto_reject"))
        if reject_count:
            review_summary = (
                f"{len(manual_review_checks)} check(s) - {reject_count} auto-reject on "
                f"match (0% score cap), {len(manual_review_checks) - reject_count} notes-only"
            )
        else:
            review_summary = f"{len(manual_review_checks)} check(s) - notes only, never affects score"
        print(
            f"  manual review: {tests_dir / 'manual_review.json'}  "
            f"({review_summary})"
        )
    else:
        print("  manual review: none (tests/manual_review.json not found)")
    print(
        f"  class fallback: {', '.join(sorted(class_fallback_candidates)) or '(none inferred)'} "
        f"- a submission missing .java for one of these but with a matching .class is still "
        f"graded, capped at 50% (see README)"
    )

    rows = []
    total = len(submissions)
    for i, sub in enumerate(submissions, start=1):
        student_id = sub.student_id
        build_key = str(i)
        row = grade_student(
            student_id, build_key, sub.java_files, sub.notes, test_files, test_classes,
            junit_jar, build_root, args.timeout, args.keep_build, rubric, required_classes,
            failed_build_root,
            class_search_root=sub.class_search_root,
            zip_needed_deeper_extraction=sub.zip_needed_deeper_extraction,
            class_fallback_candidates=class_fallback_candidates,
            not_an_archive=sub.not_an_archive,
            manual_review_checks=manual_review_checks,
        )
        rows.append(row)
        if row["compiled"] == "no":
            if "STRUCTURE ERROR" in row["notes"]:
                status = "STRUCTURE ERROR"
            elif "COMPILE ERROR" in row["notes"]:
                status = "COMPILE ERROR"
            else:
                status = "NO SOURCE FILES"
            print(f"[{i}/{total}] {student_id}: {status} (score {row['score']})")
        else:
            cap_suffix = ""
            if row["score_cap"]:
                cap_suffix = f" (capped at {row['score_cap']}: {short_cap_reason(row['notes'])})"
            print(
                f"[{i}/{total}] {student_id}: compiled, "
                f"{row['tests_passed']}/{row['tests_total']} tests passed "
                f"(score {row['score']:g}/{row['max_score']:g}){cap_suffix}"
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
