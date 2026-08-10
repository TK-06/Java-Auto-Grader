# Submission structure baseline

## Problem

Grading today only asks two questions: does the submission compile, and how
many official JUnit tests pass. Neither question actually enforces the
assignment's intended project structure. A submission that crams every
class's logic into one file, or that pads its submission with extra
unrelated classes, can still compile and score fine as long as it happens to
expose whatever the official tests call. There's no check that a submission
is actually *shaped* the way the assignment asked for.

This surfaced while auditing the 2026-08-10 grading run: after fixing a
grader bug that was zeroing out well-structured submissions (see the
`v1.4.0` commit), the natural follow-up question was whether the grader
would have caught a submission that *doesn't* follow the expected structure
at all - and it wouldn't have.

## Goals

- Catch a submission missing one of the week's required top-level classes
  (e.g. no `Station.java` at all) with a specific, readable reason, instead
  of a wall of `cannot find symbol` errors from every file that references
  the missing one, or - worse - a submission that happens to compile anyway
  because it inlined everything into one class.
- Catch a submission containing unexpected extra classes beyond what the
  assignment asked for.
- Make the check opt-in per week, authored the same way `tests/rubric.json`
  already is: a TA hands a future Claude session that week's assignment
  materials, and the config gets written from that.

## Non-goals

- **Required method signatures.** Compilation against the official JUnit
  tests already does this job, accurately: a renamed or wrong-arity method
  fails compilation with a real, specific error. A separate regex-based
  signature checker would duplicate that without being as trustworthy - it
  would either be too strict (rejecting valid code compilation would have
  accepted) or too loose to catch anything compilation doesn't already
  catch. Explicitly out of scope per the "just have JUnit do its job"
  direction during design.
- **Submission packaging/naming** (zip structure, filename conventions like
  the `_w1_q1` suffix). Separate concern, not addressed here.
- **Auto-deriving the config from a reference solution.** The config is
  hand-authored (by a future Claude session reading the assignment spec),
  not computed from starter/reference code.

## Design

### Config: `tests/structure.json` (optional)

Sits next to `tests/rubric.json`, same optionality pattern: if the file
isn't present for a given week, the check is skipped entirely and grading
behaves exactly as it does today. This means rolling it out is purely
additive - no existing week's grading changes unless a TA adds this file
for that week.

```json
{
  "required_classes": ["CPTSMachine", "Station", "Ticket"]
}
```

Deliberately just class names, not filenames or signatures. By the time
this check runs, `grade.py` has already resolved every student file to its
real public top-level type name (`resolve_java_filename`, driven by
`PUBLIC_TYPE_RE`) regardless of what the file was originally called on
disk - so "is there a class named `Station`" and "is there a file named
`Station.java`" are already the same question in the build directory this
check inspects.

### Where it runs

Right after `prepare_build_dir` returns, before `compile_submission_with_fallback`
is called - operating on the same normalized, flattened view of the
submission that's about to be compiled (Main.java already excluded,
over-nested package prefixes already rewritten to their canonical form,
duplicate/colliding files already resolved). Running it here means it
reuses all of that normalization instead of re-implementing any of it.

On a violation: `row["compiled"] = "no"`, and the note is prefixed
`STRUCTURE ERROR: ...`, treated exactly like a compile error - including
being picked up by the existing "preserve the build dir for anything that
fails to compile" logic (`results/failed_builds/<id>__<n>/`) with no
additional code, since that logic already keys off `row["compiled"] == "no"`
and the build directory already exists at this point.

If there's more than one violation (e.g. missing `Station.java` *and* a
stray `Helper.java`), all of them are reported together in one note, not
just the first one found.

### "Extra" detection reuses the leftover-test exclusion logic

A prior week's leftover test file (e.g. `TestStation.java` sitting next to
this week's official `TestStation2.java`) is already a known, tolerated
category - handled by `find_extra_test_files` (added in `v1.4.0` for the
compile-fallback fix). The structure check calls the same function and
excludes anything it identifies as a leftover test file from the "extra"
check, so enabling `structure.json` for a week never starts flagging
something that was already an accepted, unscored category.

Anything left over after excluding: required classes, official test files,
and recognized leftover test files, is reported as an unexpected extra
file.

### New functions in `grade.py`

- `load_structure_baseline(tests_dir: Path) -> list[str] | None` - mirrors
  `load_rubric`. Returns `None` if `tests/structure.json` doesn't exist.
  If it exists but is malformed (missing/wrong-typed `required_classes`),
  fails loudly at startup (`sys.exit`), the same way a misconfigured
  `lib/` directory already does - a broken config for a whole grading run
  should never be discovered as a silent per-student side effect.
- `check_structure_baseline(build_dir: Path, official_names: set[str], required_classes: list[str]) -> list[str]` -
  returns a list of human-readable violation strings (empty if the
  submission matches the baseline).

`grade_student` gains one new parameter, `required_classes: list[str] | None`,
threaded through from `main()` the same way `rubric` already is.

## Testing

- `load_structure_baseline`: absent file returns `None`; a valid file
  parses correctly; a malformed file exits with a clear error.
- `check_structure_baseline`: all-required-present-and-nothing-else returns
  no violations; a missing required class is reported; an unexpected extra
  class is reported; a recognized leftover test file is NOT reported as
  extra; multiple simultaneous violations are all reported together.
- One `grade_student`-level integration test (matching the existing
  `TestGradeStudentPreservesFailedBuilds` style) confirming a submission
  missing a required class ends up `compiled == "no"` with a `STRUCTURE
  ERROR` note, and its build directory is preserved under
  `failed_build_root` the same way a real compile error's is.
