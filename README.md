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
   tests passed/failed) and `results/scores.csv` (just `student_id,score` for quick
   gradebook upload).

**Score = number of tests passed** by default (not a percentage) — `max_score` is recorded
alongside it in `grades.csv` so you can see what it was out of. A submission that doesn't
compile scores 0.

If different test cases are worth different marks (see **Weighted scoring** below), drop a
`rubric.json` next to that week's tests and `score`/`max_score` become the weighted point
total instead — no other change needed.

## One-time setup

1. **Install a JDK** (not just a JRE) — `javac` must be on PATH. Check with:
   ```
   javac -version
   ```
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

**Package declarations are stripped from student files before compiling** (and any
now-dangling `import` referencing that package is stripped too). This grader assumes every
class — student and official test alike — lives in one flat, unnamed package, since your
`tests/*.java` files never declare one. Some IDEs (IntelliJ especially, when a project's
source root isn't marked correctly — plain `main/java` folders instead of a configured
`src` root) auto-insert a real `package main.java;` line into student files. Left in place,
the student's classes would compile into that named package while the unnamed-package
official test can't see them — `cannot find symbol`, even though the code is otherwise
fine. Both the stripped package and any import cleaned up alongside it are noted in
`notes`.

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

### 3. Run it

```
python grade.py
```

Progress prints as it goes; when it's done, check `results/grades.csv` (detailed, with
a `notes` column explaining any 0 score) and `results/scores.csv` (just IDs and scores).

### Useful flags

```
python grade.py \
  --submissions submissions \
  --tests tests \
  --lib lib \
  --out results/grades.csv \
  --scores-out results/scores.csv \
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
  tests/                   <- this week's official test file(s), tracked in git
  results/
    grades.csv              <- gitignored output
    scores.csv               <- gitignored output
  build_tmp/               <- gitignored scratch space, created/cleaned automatically
```

## Ideas for extending

- A `config.yaml` per week (test folder, timeout, weight) instead of CLI flags
- Basic plagiarism screening (MOSS or a diff-based similarity pass) before grading
- Parallelize student runs with a process pool (keep timeouts — don't let one infinite loop
  block the others)
