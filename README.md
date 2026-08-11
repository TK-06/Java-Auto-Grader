# Auto-Grader for Data Structures (Java + JUnit)

Compiles every student's submission together with the week's official JUnit tests, runs
the tests, and writes the results to a CSV. Built for TAs grading a Data Structures course
where students submit either a full Eclipse project (zipped or exported as a `.jar`) or a
single `.java` file.

## What it does

For every student submission, it:
1. Finds all their `.java` files (inside a folder, a `.zip`, a `.jar`, or a single loose
   file — a JAR is just a ZIP file with a manifest, so both archive types work the same way).
2. Drops in the week's official test file(s), **overwriting any copy of the same file the
   student bundled in their own submission** — so the official test always wins even though
   Eclipse projects normally ship with the test file already in `src/`.
3. Compiles everything together with `javac`.
4. Runs the tests with the JUnit Console Launcher and parses its XML reports (not just the
   printed summary), so it knows the pass/fail result of every individual test method.
5. Writes one row per student to `results/grades.csv` (detailed, including which named
   tests passed/failed) and `results/mcvScore.csv` (no header row, just `student_id,score`
   per line, ID stripped down to the bare number, for quick MyCourseVille gradebook upload).

**Score = number of tests passed** by default (not a percentage) — `max_score` is recorded
alongside it in `grades.csv` so you can see what it was out of. A submission that doesn't
compile scores 0. A submission missing `.java` source is still graded from a matching
`.class` file when one is found, capped at 50%/90%/45% depending on why — see **Compiled-only
submissions** below.

If different test cases are worth different marks (see **Weighted scoring** below), drop a
`rubric.json` next to that week's tests and `score`/`max_score` become the weighted point
total instead — no other change needed.

## Grading policy (SLA)

What every submission can expect, regardless of week:

- **Late submissions are docked 10% per day late**, applied as a cap the same way the rules
  below are — 1 day late caps the score at **90%** of what it would otherwise be, 2 days at
  **80%**, and so on down to **0%** at 10+ days late. Combines multiplicatively with any
  other cap in effect, same as the 50%/90% rules below (e.g. a `.class`-only submission 2
  days late: 0.5 × 0.8 = **40%**). The script has no deadline awareness of its own — this is
  a manual step: work out each submission's days-late and apply the cap by hand (or
  pre-multiply the `score` column before writing `mcvScore.csv`) before uploading.
- **Missing a required class entirely** (no `.java` *and* no `.class` anywhere in the
  submission) → **0**, rejected before compiling with `STRUCTURE ERROR` in `notes`, listing
  every class that's missing. See [2c. Required project
  structure](#2c-optional-required-project-structure).
- **Submitted only a compiled `.class`** for a required class, no `.java` source anywhere →
  still graded, from the bytecode, but capped at **50%** of that week's max score, since
  there's no source to verify. See [2d. Compiled-only
  submissions](#2d-compiled-only-submissions-class-instead-of-java).
- **Submitted a `.zip` that needed extra digging** to find anything gradable (e.g. a `.zip`
  wrapping a `.jar` instead of the project directly) → capped at **90%**, regardless of
  whether what was eventually found was source or compiled classes. Combines
  multiplicatively with the 50% rule above when both apply (**45%** total).
- **Doesn't compile** (with the official tests dropped in, replacing any copy the student
  bundled) → **0**, with the exact `javac` error saved in `notes` so the reason is always
  visible, not just the score.
- **Target Java 17 language level, even if compiled with a newer JDK.** Grading runs on
  **JDK 25** — a student's `.class` file compiled to a *newer* bytecode version than JDK 25
  supports can't be loaded at all (`UnsupportedClassVersionError` on every test, or a `bad
  class file`/`wrong version` compile error) and scores **0**, same as any other
  compile/run failure. Setting the project's compiler compliance/source-target level to 17
  (Eclipse: Project Properties → Java Compiler) keeps the submission's bytecode version well
  under that ceiling regardless of which JDK a student has installed locally.

Every cap and rejection is explained in that student's `notes` column, and `uncapped_score`
always records what the result would have been with no cap applied, so the pre-cap number is
auditable even when a cap brought the final `score` down.

## One-time setup

1. **Install JDK 25** (not just a JRE) — `javac` must be on PATH. Check with:
   ```
   javac -version
   ```
   JDK 25 is the grading environment's own requirement (see **Grading policy** above) — a
   student submission is expected to target Java 17, well under it. Grading with an older
   JDK than 25 risks incorrectly failing a compliant student whose IDE's default project
   settings still embedded a slightly newer bytecode version than your compiler supports.
2. **Get the JUnit Console Launcher jar** and put it in `lib/` — see `lib/README.md` for
   the download link. Only keep one jar in that folder.

## Weekly workflow

### 1. Put this week's official test file(s) in `tests/`

Drop the `.java` file(s) with your `@Test` methods directly into `tests/` (flat, no
subfolders needed). Whatever was there from last week gets replaced — that's the only
thing you change week to week.

### 2. Put student submissions in `submissions/`

`submissions/` is **gitignored** on purpose — real student code should never end up
committed to this shared repo. Each student's submission can be any of these four
shapes, mixed freely in the same folder:

| What the student sent you | Where it goes | Example |
|---|---|---|
| A zipped Eclipse project (`.zip`) | Drop the `.zip` straight into `submissions/`, don't unzip it | `submissions/20304050.zip` |
| A `.jar` export of an Eclipse project (some students export as JAR instead of ZIP) | Drop the `.jar` straight into `submissions/`, don't extract it | `submissions/20304050.jar` |
| An unzipped project folder | Drop the whole folder in, any nesting/structure inside is fine | `submissions/20304050/src/Calculator.java` |
| A single `.java` file (some weeks you'll only ask for one file) | Drop the file straight in | `submissions/20304050.java` |

**Name the zip/jar/folder/file after the student's ID or username** — that's how the script
identifies whose grade is whose, and it becomes the `student_id` column in the CSV. If
your Canvas/Gradescope bulk download already names things that way, you can usually
point `--submissions` straight at the extracted download folder.

You do **not** need to strip out the test files a student bundled in their own Eclipse
project (e.g. `TestPointsLinkedList.java` sitting in their `src/` alongside their own
code) — the script detects the name collision with your official copy in `tests/` and
discards the student's version automatically, noting it in the `notes` column.

**`Main.java` is always excluded from grading**, by course policy. Recent IntelliJ project
templates (JDK 21+) auto-generate a scaffold `Main.java` using preview-feature syntax
(`void main() { ... }` with no class) that plain `javac` rejects without `--enable-preview`.
Students routinely leave this untouched since it isn't part of any assignment — without
this exclusion, that one irrelevant leftover file would fail the whole submission's
compile, even though the actual assignment code is fine. Any student file that resolves to
`Main.java` is skipped and noted in `notes`, exactly like a colliding test file.

**A student's package declaration is stripped before compiling, but only if none of this
week's official test files actually need it** (and if it's stripped, any now-dangling
`import` of that package elsewhere in the submission is stripped too). Some weeks' official
tests assume every class lives in one flat, unnamed package (no `package`/`import` at all);
others explicitly `import application.CPTSMachine;`, meaning that package is a real project
requirement. The grader checks what `tests/*.java` itself declares/imports before touching
anything, so it never strips a package the official test still needs. This exists because
some IDEs (IntelliJ especially, when a project's source root isn't marked correctly — plain
`main/java` folders instead of a configured `src` root) auto-insert a real
`package main.java;` line into student files that were never meant to have one, which
otherwise causes `cannot find symbol` against an unnamed-package test even though the code
is fine. Both the stripped package and any import cleaned up alongside it are noted in
`notes`.

If a package is required (like `application` above) but a student's declaration is that
name *plus an extra prefix* — e.g. `Q1_toStudent.application`, because their IDE inferred
the package from a source-root folder literally named after the assignment — the grader
rewrites it down to the required name instead of stripping it, and rewrites any sibling
file's `import Q1_toStudent.application.Foo;` to match. Deleting it outright would leave
the official test's own `import application.CPTSMachine;` unable to resolve. This is noted
in `notes` as "rewrote package declaration ... (nested under an extra prefix ...)", distinct
from a plain strip.

### 2b. (Optional) Weighted scoring — some test cases worth more than others

By default every passing test is worth 1 point. If your rubric gives different test cases
different marks (e.g. a "how to mark" doc saying `testCalculatePrice() 2 marks`), create
`tests/rubric.json` mapping each test class → test method → points:

```json
{
    "TestCPTSMachine2": {
        "testIsStationExisted": 1,
        "testAddStation": 1,
        "testBuyTicketIllegal": 1,
        "testBuyTicketLegal": 1
    },
    "TestStation2": {
        "testConstructor": 1,
        "testSetName": 1,
        "testSetNumber": 1
    },
    "TestTicket2": {
        "testSetTypeLegal": 1,
        "testSetTypeIllegal": 1,
        "testSetStation": 1,
        "testCalculatePrice": 2,
        "testGetDescription": 2
    }
}
```

Use the class name only (no package) and the method name only (no `()`). With this in
place:
- `score` = sum of points for every rubric test the student passed
- `max_score` = sum of every point value in the rubric (14 in the example above) — this is
  the same for every student regardless of what their submission did, so it's your "out of"
- `grades.csv` gets a `passed_tests` / `failed_tests` column listing exactly which named
  tests passed or failed, e.g. `TestTicket2.testCalculatePrice`
- If a rubric test never shows up in a student's results at all (not just failed —
  genuinely missing, e.g. because a whole test class failed to load), that's called out in
  `notes` so you notice rather than it silently scoring 0
- Extra `@Test` methods found that *aren't* in the rubric (e.g. a sanity-check test file the
  student bundled alongside your official one, under a different filename) are listed in
  `notes` too, but don't affect the score either way

**No `tests/rubric.json`?** Nothing changes — `score` stays the flat "1 point per passed
test" count exactly as before. This is entirely opt-in, per week.

### 2c. (Optional) Required project structure

Compiling against the official tests already catches a wrong method signature — that
fails compilation with a real, specific error, so there's no separate check for it here.
What it *doesn't* catch is a submission missing one of the classes the assignment actually
requires entirely (e.g. no `Station.java` at all): that fails with a wall of `cannot find
symbol` errors from every file that referenced it, instead of one specific reason. If you
want that caught explicitly, create `tests/structure.json`:

```json
{
    "required_classes": ["CPTSMachine", "Station", "Ticket"]
}
```

Just class names — not filenames, not signatures. By the time this check runs, every
student file has already been resolved to its real public type name regardless of what it
was originally called on disk, so "is there a class named `Station`" and "is there a file
named `Station.java`" are the same question.

This only checks for *missing* required classes, not extra ones — the mental model is "if
we swapped in the official test file on the student's own machine, would it still work,"
and an extra class sitting unused alongside the required ones doesn't break that. With
this in place, a submission missing one or more required classes is rejected *before*
compiling — `compiled` is `no` and `notes` starts with `STRUCTURE ERROR:`, listing every
missing class, not just the first.

**No `tests/structure.json`?** Nothing changes — no structure check runs, exactly as
before. Entirely opt-in, per week.

### 2d. Compiled-only submissions (`.class` instead of `.java`)

A submission that's missing `.java` source for one of the classes this week's tests
actually need — but does include that class's own precompiled `.class` somewhere inside
it (a runnable-jar export that forgot to include source is the usual cause) — is still
graded from the bytecode instead of being rejected. "Which classes are actually needed"
comes from `tests/structure.json`'s `required_classes` when you've set that up, **and**
is inferred automatically from what `tests/*.java` itself imports/constructs either way —
so this works every week, with or without `structure.json`. A class missing *both* forms
(no `.java` and no `.class` anywhere) is unaffected by any of this — still a normal
`STRUCTURE ERROR` (if `structure.json` is set up) or compile error, exactly as before.

Two independent penalties apply to the final score when this kicks in, since there's no
source to actually verify:

- **Capped at 50%** — one or more required classes had no `.java` source at all, only a
  `.class` we found and used.
- **Capped at 90%** — the submission was a `.zip` whose *first* unzip alone didn't turn up
  anything gradable, meaning we had to dig further (e.g. a `.zip` wrapping a single `.jar`)
  to find usable content — regardless of whether what we eventually found was source or
  compiled classes. A `.jar`/`.class` submitted directly (no `.zip` wrapper) never triggers
  this one, only the 50% rule above can apply to it.
- **Both at once → 45%** (0.5 × 0.9) — e.g. a `.zip` wrapping a jar that's *also* missing
  source for a required class.

`grades.csv` gets two new columns for this: `uncapped_score` (what the row would have
scored with no cap applied, always populated) and `score_cap` (`50%` / `90%` / `45%`, blank
when no cap applied). `notes` explains why, e.g. `SCORE CAPPED AT 50%: used precompiled
.class instead of .java source for required class(es): Item, MisaShop`. `mcvScore.csv` is
unaffected in format — it just carries through whatever the final (already-capped) `score`
ended up being, same as always.

### 3. Run it

```
python grade.py
```

Progress prints as it goes; when it's done, check `results/grades.csv` (detailed, with
a `notes` column explaining any 0 score) and `results/mcvScore.csv` (just bare IDs and
scores, ready for MyCourseVille).

### Useful flags

```
python grade.py \
  --submissions submissions \
  --tests tests \
  --lib lib \
  --out results/grades.csv \
  --scores-out results/mcvScore.csv \
  --timeout 30 \
  --keep-build   # don't delete build_tmp/, useful for debugging a student's compile error
```

If a student's row looks wrong, rerun with `--keep-build` and check the extra `build dir:
...` line printed under that student's progress line — it points at exactly what got
compiled (student files with any skipped/colliding ones noted, plus the official test
files) and, if it compiled, the `.class` files in `<build dir>/classes/`. (The folder name
itself is a short hash, not the student's ID — long/messy real-world filenames blow past
Windows' path length limit if used directly for a nested build path, so the actual
student_id only ever appears in the CSV, never in a filesystem path.)

## Known behavior / edge cases handled

- **Compile errors** → score 0, the javac error is saved in `notes` so you can see why at
  a glance (student's own build path is stripped out of the message for readability).
- **Infinite loops / hangs** → killed after `--timeout` seconds (default 30), scored 0
  with a note, rather than hanging the whole grading run. This is enforced with a hard
  process-tree kill, not just Python's `subprocess` timeout, which isn't reliable against
  a silently-hung JVM on Windows.
- **Skipped tests** (e.g. `@Disabled`) are not counted in `tests_total`; noted in `notes`.
- **Long or messy submission filenames** (LMS/browser download artifacts sometimes append
  junk like `-1522399-17860067636260` before the extension) never hit Windows' 260-character
  path limit during extraction, since the filesystem folder used is always a short hash —
  the real filename is preserved as-is for the `student_id` column.
- **0 tests found** (student renamed/overwrote a class the test depends on) is reported
  distinctly from a compile failure.
- **A single-file submission renamed to the student's ID** by the LMS (e.g. `87654321.java`
  containing `public class Calculator`) is automatically re-named to `Calculator.java`
  when compiled, since Java requires a public type's filename to match its name.
- **Corrupt/empty zip or jar files** are reported in `notes` rather than silently skipped.
- **Two submissions resolving to the same student_id** (e.g. both a `switch/` folder and a
  `switch.jar` sitting in `submissions/` at once) prints a `WARNING` before grading starts
  and both still get graded as separate rows — nothing is silently merged or dropped.
- One student's broken submission can never crash the whole run — every per-student step
  is wrapped so a batch of 60 always finishes even if one submission is garbage.
- Results are parsed from JUnit's XML reports (not the printed text summary), so per-test
  pass/fail is exact even if a student's code prints to stdout/stderr during a test.

## Project layout

```
grading/                  <- repo root
  grade.py
  README.md
  lib/
    junit-platform-console-standalone-*.jar   <- you download this, not committed
    README.md
  submissions/             <- gitignored; this week's real student work goes here
  tests/                   <- gitignored; this week's official test file(s) + rubric.json
  results/
    grades.csv              <- gitignored output
    mcvScore.csv             <- gitignored output, bare student IDs for MyCourseVille
  build_tmp/               <- gitignored scratch space, created/cleaned automatically
```

## Ideas for extending

- A `config.yaml` per week (test folder, timeout, weight) instead of CLI flags
- Basic plagiarism screening (MOSS or a diff-based similarity pass) before grading
- Parallelize student runs with a process pool (keep timeouts — don't let one infinite loop
  block the others)
