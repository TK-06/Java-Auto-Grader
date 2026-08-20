# Auto-Grader for Data Structures (Java + JUnit)

Compiles every student's submission together with the week's official JUnit tests, runs
the tests, and writes the results to a CSV. Built for TAs grading a Data Structures course
where students submit a full Eclipse project, packaged as either a `.zip` or a `.jar`.

## What it does

For every student submission, it:
1. Finds all their `.java` files inside the archive (a `.zip` or a `.jar` — a JAR is just a
   ZIP file with a manifest, so both work the same way). A submission that isn't a packaged
   archive at all — a loose `.java` file, or an unpackaged folder, dropped straight into
   `submissions/` — is rejected before compiling; see **Grading policy** below.
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
submissions** below. A submission can also be capped at 0% despite compiling and passing
every test, if a `tests/manual_review.json` check with `"auto_reject": true` matched — see
**Manual-review flags** below.

If different test cases are worth different marks (see **Weighted scoring** below), drop a
`rubric.json` next to that week's tests and `score`/`max_score` become the weighted point
total instead — no other change needed.

## Grading policy (SLA)

What every submission can expect, regardless of week:

- **Late submissions are docked 10% per day late**, applied as a cap the same way the rules
  below are — 1 day late caps the score at **90%** of what it would otherwise be, 2 days at
  **80%**, and so on down to **0%** at 10+ days late. Combines multiplicatively with any
  other cap in effect, same as the 50%/90% rules below (e.g. a `.class`-only submission 2
  days late: 0.5 × 0.8 = **40%**). The script has no deadline awareness of its own —
  `check_lateness.py` (see **Weekly workflow** below) computes each submission's real
  days-late from the original MCV export, but applying that to the score is still a manual
  step: apply the cap by hand (or pre-multiply the `score` column before writing
  `mcvScore.csv`) before uploading.
- **Submitted as bare `.java` source instead of a packaged archive** (a loose `.java` file,
  or an unpackaged folder of them, dropped straight into `submissions/` — commonly an LMS
  bulk-download artifact bundling two individually-uploaded files together) → **0**, rejected
  before compiling with `STRUCTURE ERROR` in `notes`, regardless of whether the source itself
  would otherwise compile and pass. See [2. Put student submissions in
  `submissions/`](#2-put-student-submissions-in-submissions).
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
- **Matched a `tests/manual_review.json` check with `"auto_reject": true`** (opt-in, per
  week — e.g. a marking guide that says to reject an instanceof-chain workaround) → capped
  at **0%**, even though it compiled and passed every test. Combines multiplicatively with
  the other caps too, but since it's ×0 it always wins regardless. See [2e. Manual-review
  flags](#2e-optional-manual-review-flags-for-things-junit-cant-catch).
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
2. **The JUnit Console Launcher jar is already committed in `lib/`** — a fresh clone works
   with no download step. See `lib/README.md` only if you need to bump it to a newer version
   later; keep just the one jar in that folder either way.

## Weekly workflow

### 1. Put this week's official test file(s) in `tests/`

Drop the `.java` file(s) with your `@Test` methods directly into `tests/` (flat, no
subfolders needed). Whatever was there from last week gets replaced — that's the only
thing you change week to week.

### 2. Put student submissions in `submissions/`

`submissions/` is **gitignored** on purpose — real student code should never end up
committed to this shared repo. **Every submission must be a packaged archive** — a `.zip`
or a `.jar` — dropped straight into `submissions/` exactly as received, don't unzip or
extract it yourself:

| What the student sent you | Where it goes | Example |
|---|---|---|
| A zipped Eclipse project (`.zip`) | Drop the `.zip` straight into `submissions/`, don't unzip it | `submissions/20304050.zip` |
| A `.jar` export of an Eclipse project (some students export as JAR instead of ZIP) | Drop the `.jar` straight into `submissions/`, don't extract it | `submissions/20304050.jar` |

**Anything else is rejected outright** — scored 0 with `STRUCTURE ERROR` in `notes`, even if
the code itself is fine:
- **An unzipped project folder** dropped directly into `submissions/`
- **A loose `.java` file** dropped directly into `submissions/`

This mainly shows up as an LMS bulk-download artifact rather than something a student did
on purpose: a student who uploads two separate `.java` files instead of one archive often
gets them bundled by the download tool into a same-named folder (e.g.
`submissions/20304050/` containing `Bot-1237131-....java` and `Part-1237131-....java`) -
which is exactly what this catches. Submitting a packaged archive is part of the
assignment's required format, not just a convenience for this grader, so this is never
fixed by hand on your end — have the student resubmit as a `.zip`/`.jar`.

**Name the zip/jar after the student's ID or username** — that's how the script
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
- **Capped at 0%** — a `tests/manual_review.json` check with `"auto_reject": true` matched
  (see [2e](#2e-optional-manual-review-flags-for-things-junit-cant-catch)). Multiplies in
  with the other two exactly the same way, but since it's ×0 it always wins outright
  regardless of what else applied.

`grades.csv` gets two new columns for this: `uncapped_score` (what the row would have
scored with no cap applied, always populated) and `score_cap` (`50%` / `90%` / `45%` / `0%`,
blank when no cap applied). `notes` explains why, e.g. `SCORE CAPPED AT 50%: used precompiled
.class instead of .java source for required class(es): Item, MisaShop`. `mcvScore.csv` is
unaffected in format — it just carries through whatever the final (already-capped) `score`
ended up being, same as always, so a `0%`-capped student uploads as an actual 0 with no
extra step on your part.

**When the found `.class` is compiled under a different package than the test expects**
(e.g. baked as `package main.java;` — the same common IDE default that source submissions
already get forgiven for, just with no source left to strip it from) it still gets a real
shot: a throwaway copy of just the affected official test file gets a matching `import`
added (never the real `tests/*.java` — deleted along with the rest of that student's build
directory once grading moves on) and compilation is attempted against that. If it compiles,
the class evidently works fine once the test can actually see it, so it's graded for real
against the student's real, unmodified bytecode and capped at 50% exactly as above — this
is what recovers credit that's rightfully earned instead of scoring a correct submission 0
over an import path. If it doesn't compile, everything reverts and the submission falls
back to exactly the STRUCTURE ERROR / compile error it would have gotten otherwise, with a
note explaining the attempt was made and didn't pan out.

### 2e. (Optional) Manual-review flags for things JUnit can't catch

Some weeks' marking guides call out a check that can't be expressed as a test assertion —
e.g. *"reject solutions that re-implement behaviour with instanceof chains instead of
overriding."* A student who does this can still pass every test (the behavior looks
identical from the outside), so no `@Test` method can ever catch it — it needs a human to
actually read the code. If you want that flagged automatically instead of eyeballing every
submission yourself, create `tests/manual_review.json`:

```json
{
    "checks": [
        {
            "pattern": "instanceof",
            "reason": "possible instanceof-chain workaround instead of proper overriding",
            "exclude_classes": ["Boss"],
            "auto_reject": true
        }
    ]
}
```

- `pattern` — a regex, searched against each student `.java` file's raw source.
- `reason` — free text, copied straight into the note so you don't have to re-open this
  file to remember why something got flagged.
- `exclude_classes` (optional) — class names exempt from *this* check, e.g. a class whose
  own game rules legitimately require `instanceof` (the `Boss` example above).
- `auto_reject` (optional, defaults to `false`) — see below.

`checks` is a list, so one week can flag more than one unrelated pattern (e.g. `instanceof`
*and* a banned import) without needing a second file.

A match always appends `MANUAL REVIEW: <reason> - found in <File.java> (line N), ...` to
that student's `notes`. What happens to the score depends on `auto_reject`:

- **`auto_reject` absent or `false` (the default)** — the note is **all** that happens. It
  never touches `compiled`, `tests_passed`, `score`, or any cap; it's purely a flag for you
  to read and decide on, the same way a TA would circle something suspicious on a printed
  page. A submission can be flagged and still score full marks, or fail to compile and also
  be flagged — the two are completely independent. Use this when the marking guide says
  something like "flag for review" or the call genuinely needs human judgment.
- **`auto_reject: true`** — a match *also* forces a hard 0% score cap, same mechanism as the
  50%/90% caps in [2d](#2d-compiled-only-submissions-class-instead-of-java) (multiplies in
  with any other cap already in play, and since it's ×0 it always wins). `uncapped_score`
  still records what the score would have been, `score_cap` shows `0%`, and `notes` gets a
  second line, `SCORE CAPPED AT 0%: manual review check(s) require rejection: ...`, right
  after the `MANUAL REVIEW` one. `mcvScore.csv` picks up the real 0 automatically, so there's
  no separate manual step before uploading. Use this only when the marking guide is
  unambiguous ("reject", "0 for this") **and** no legitimate solution could ever trigger the
  pattern — a false positive here silently zeroes a real student with no review step, so
  when in doubt leave it `false` instead.

The console progress line also gets a short inline reason whenever any cap (50%/90%/0%)
applies, e.g. `... (score 5.5/11) (capped at 50%: failed to include source file)` for a
partial cap. Once the final score is exactly 0, the percentage is dropped from the line
entirely — a cap that lands on zero doesn't need its number stated — so it reads
`... (score 0/11) - rejected by manual review` instead. The full detail (percentage
included) always still lives in `grades.csv`'s `notes` column regardless; this is just a
quick heads-up while a run is in progress.

**No `tests/manual_review.json`?** Nothing changes — no scan runs, exactly as before.
Entirely opt-in, per week. **The file must be absent, not present with an empty `checks`
list** — a leftover `{"checks": []}` from a previous week that no longer needs any check is
treated as a malformed config, the same as a `checks` entry missing `pattern`/`reason`, and
`grade.py` exits immediately with an error instead of silently running zero checks. This is
deliberate (see the docstring on `load_manual_review_checks` in `grade.py`): a broken config
for the whole run should fail loudly at startup, not get discovered one student in. When a
new week needs no manual-review check, delete the file entirely rather than emptying it out.

### 2f. (Optional) Stub-only submissions — "Stubs only = 0"

Some marking guides say explicitly that submitting the unedited starter template (no attempt
made) scores zero, not whatever partial credit the official tests happen to award by accident —
a stub can still legitimately pass a base-behavior test that doesn't depend on anything the
student was supposed to implement. If you want that enforced automatically, create
`tests/starter/`, one file per required class named exactly `ClassName.java`, containing that
class's content **exactly as given to students** (straight from the week's `toStudent` zip,
untouched):

```
tests/starter/
  Unit.java
  Warrior.java
  Mage.java
  Boss.java
```

With this in place, a submission is capped at **0%** only if **every** file in `tests/starter/`
matches the student's corresponding class byte-for-byte (after normalizing line endings and
trailing whitespace — nothing else). A student who implemented even one of the tracked classes
for real is never touched by this, no matter how broken the rest of their submission is. A
required class the check can't find at all in the submission is not treated as a match either —
that's `structure.json`'s or the `.class`-fallback path's call, not this one's.

`notes` gets a `STUB-ONLY SUBMISSION: ...` line and, since this reuses the same cap mechanism as
`manual_review.json`'s `auto_reject`, also a `SCORE CAPPED AT 0%` line — `uncapped_score` still
records what the raw JUnit result would have been.

**No `tests/starter/` folder (or an empty one)?** Nothing changes — no comparison runs, exactly
as before. Entirely opt-in, per week.

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

### 4. (Optional) Check for late submissions

The late-penalty policy above (10% off per day) needs a deadline to check against, which
`grade.py` itself has no concept of. `check_lateness.py` computes it separately, as a standalone
script:

```
python check_lateness.py <zip1> [<zip2> ...] --deadline 2026-08-19T23:59:00
```

It reads the original MyCourseVille bulk-export zip(s) directly — never `submissions/`, since
renaming each student's file down to `<studentID>.<ext>` during extraction already discarded
the original filename the real timestamp was embedded in. For each student it picks their
latest submission to `--question` (default `2`; pass a different number for another
question — nothing in the script itself is question-specific), decodes the real submission
time from the filename, and prints `days_late` plus the resulting score multiplier. It only
reads and prints — applying a result to `grades.csv`/`mcvScore.csv` is a separate, deliberate
manual edit, same as any other score override.

**Keep the original zip file(s) around until this has been run** — once a submission is
renamed into `submissions/`, its real timestamp can no longer be recovered from anything in
this repo.

## Reading the results

`results/grades.csv` has one row per student, with these columns:

| Column | What's in it |
|---|---|
| `student_id` | Taken straight from the submission's filename/folder, exactly as submitted (not stripped down to the bare number — see `mcvScore.csv` below for that). |
| `compiled` | `yes` or `no`. |
| `tests_passed` | Count of official tests that passed. |
| `tests_total` | Passed + failed official tests (skipped/`@Disabled` tests aren't counted in either). |
| `score` | Points earned — 1 per passed test by default, or the weighted sum from `tests/rubric.json` if present — **after** any cap from the Grading policy is applied. |
| `max_score` | Total points possible this week (test count, or the rubric's point total). |
| `uncapped_score` | What `score` would have been with no cap applied at all — always populated, even when no cap ends up binding, so the pre-cap number is auditable. |
| `score_cap` | The cap percentage applied (`50%` / `90%` / `45%` / `0%`), blank if none applied. |
| `passed_tests` | `;`-separated `ClassName.testMethod` for every passed test. |
| `failed_tests` | `;`-separated `ClassName.testMethod` for every failed test. |
| `failure_details` | `;`-separated `ClassName.testMethod: <assertion message>` for every failed test — the actual JUnit failure reason, so you can see why a test failed straight from the CSV instead of re-running it. |
| `notes` | Anything unusual about this submission: compile errors, `STRUCTURE ERROR`, skipped/colliding files, why a cap applied, a timed-out test run, `MANUAL REVIEW` flags (see [2e](#2e-optional-manual-review-flags-for-things-junit-cant-catch) — score-neutral unless that check has `auto_reject: true`, in which case there's also a `SCORE CAPPED AT 0%` line), etc. Empty when nothing noteworthy happened. |

`results/mcvScore.csv` is deliberately minimal — no header row, just `student_id,score` per
line, with `student_id` stripped down to the bare numeric ID (dropping any `_w1_q2`-style
tag a submission's filename carried) so it matches MyCourseVille's own records for direct
gradebook upload.

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
  check_lateness.py        <- optional; computes real submission times / late penalties, see Weekly workflow §4
  README.md
  lib/
    junit-platform-console-standalone-*.jar   <- committed; see lib/README.md to update it
    README.md
  submissions/             <- gitignored; this week's real student work goes here
  tests/                   <- gitignored; this week's official test file(s), plus optional
                              rubric.json / structure.json / manual_review.json / starter/
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
