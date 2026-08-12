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
verify. A found .class whose own compiled package could never satisfy the
official tests (e.g. baked as `package main.java;` when the tests need the
class unqualified) is excluded up front with a specific note instead of
being seeded to fail compilation with an unrelated "cannot find symbol" -
see filter_unusable_fallback_matches. A submission that only yielded
gradable content after digging past
a plain unzip (a .zip wrapping a nested jar, say) is capped at 90%,
independent of whether source was ultimately found. Both apply together
multiplicatively (0.5 x 0.9 = 45%) when both are true. uncapped_score always
records the pre-cap result; score_cap shows the cap percentage applied
("" when none); notes explains why ("SCORE CAPPED AT n%: ...").
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
    week's tests. Used by filter_unusable_fallback_matches to know, before
    ever seeding a found .class into classes/, whether a candidate that
    resolve_class_fallback_dest left nested under some directory can
    possibly work: a class the tests reference unqualified needs to land
    at the classpath root, full stop, so a candidate that's still nested
    after resolve_class_fallback_dest's own trimming never will (see that
    function's docstring for why a compiled .class's real package can't be
    rewritten the way source text can). A class instead reached via a
    qualified import is left alone here - resolve_class_fallback_dest
    already knows how to place (or correctly reject) that case."""
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


def filter_unusable_fallback_matches(
    fallback_matches: dict[str, list[Path]],
    class_search_root: Path,
    test_files: list[Path],
) -> tuple[dict[str, list[Path]], list[str]]:
    """Drop a found fallback .class candidate that resolve_class_fallback_dest
    could never place correctly, and say specifically why, instead of
    silently seeding it and letting compilation fail downstream with an
    unrelated "cannot find symbol" - the same end score either way (still
    ungradable), but a note a TA can act on without a manual javap dig.

    Only checked for classes infer_unnamed_package_classes says the tests
    need in the unnamed package: a class instead reached via a qualified
    import is left untouched, since resolve_class_fallback_dest's own
    canonical-package trimming already handles (or correctly rejects) that
    case on its own. Does NOT change resolve_class_fallback_dest's actual
    trimming logic or catch a class whose baked-in package merely happens
    to share a prefix with what's required (e.g. a genuine
    `package Q2_toStudent.logic;` for a test needing `logic`) - that's
    still left to run and fail at compile time with "class file contains
    wrong class", exactly as documented in resolve_class_fallback_dest.

    Returns the filtered {class_name: [files]} dict (a class with zero
    usable files left is dropped entirely, so it no longer counts as
    "covered" for check_structure_baseline's covered_by_class_fallback) and
    the list of explanatory notes for whatever got dropped."""
    referenced_packages = collect_referenced_packages(test_files)
    unnamed_required = infer_unnamed_package_classes(test_files)
    kept: dict[str, list[Path]] = {}
    notes: list[str] = []
    for class_name, files in fallback_matches.items():
        usable: list[Path] = []
        bad_packages: set[str] = set()
        for f in files:
            rel = f.relative_to(class_search_root)
            dest_rel = resolve_class_fallback_dest(rel, referenced_packages)
            if class_name in unnamed_required and dest_rel.parent != Path("."):
                bad_packages.add(".".join(dest_rel.parent.parts))
                continue
            usable.append(f)
        if usable:
            kept[class_name] = usable
        else:
            notes.append(
                f"found precompiled {class_name}.class elsewhere in the submission, but "
                f"it's compiled under package '{', '.join(sorted(bad_packages))}' - the "
                f"official tests need it in the default (unnamed) package, so it can't "
                f"be used as a substitute"
            )
    return kept, notes


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
        if missing_source and class_search_root is not None:
            fallback_matches = find_class_fallback_files(class_search_root, missing_source)
            if fallback_matches:
                # Drop any candidate that could never actually resolve for the
                # official tests (see filter_unusable_fallback_matches) BEFORE the
                # structure-baseline check below, so an unusable candidate never
                # counts as "covered" and masks a real STRUCTURE ERROR behind a
                # confusing downstream COMPILE ERROR instead.
                fallback_matches, unusable_notes = filter_unusable_fallback_matches(
                    fallback_matches, class_search_root, test_files
                )
                prep_notes = prep_notes + unusable_notes

        if not student_files and not fallback_matches:
            row["notes"] = "; ".join(prep_notes + ["no .java source files found"]).strip("; ")
            return row

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

        if fallback_matches:
            # Seed the discovered .class files into classes/ BEFORE compiling -
            # compile_submission puts classes_dir on javac's own -cp too, so any
            # remaining .java that references one of these classes as a
            # dependency still resolves. Each file's destination is resolved
            # against the official tests' own referenced packages (see
            # resolve_class_fallback_dest) so an extra wrapper directory in the
            # submission (e.g. Q2_toStudent/logic/Item.class) doesn't leave the
            # class undiscoverable on the classpath at package `logic`.
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
            for files in fallback_matches.values():
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
            prep_notes = prep_notes + [
                "found precompiled .class (no .java source) for required class(es): "
                + ", ".join(sorted(fallback_matches))
            ]
            if collisions:
                prep_notes = prep_notes + [
                    "WARNING: multiple .class files resolved to the same classpath "
                    "location, only the last one found was used: " + "; ".join(collisions)
                ]

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

        # Score cap policy: a submission graded from precompiled .class instead of
        # .java (fallback_matches non-empty - see above) is capped at 50% of
        # max_score, since there's no source to verify; a submission that only
        # yielded gradable content after digging past a plain unzip
        # (zip_needed_deeper_extraction - see discover_submissions) is capped at
        # 90%, for improper packaging independent of whether source was found.
        # Both apply multiplicatively when both are true (0.5 x 0.9 = 45%).
        # uncapped_score always records what the raw result would have been, for
        # audit/appeal purposes, even when no cap ends up binding.
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
