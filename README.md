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
4. Runs the tests with the JUnit Console Launcher.
5. Writes one row per student to `results/grades.csv` (detailed) and `results/scores.csv`
   (just `student_id,score` for quick gradebook upload).

**Score = number of tests passed** (not a percentage). `tests_total` is recorded alongside
it in `grades.csv` so you can see what it was out of. A submission that doesn't compile
scores 0.

## One-time setup

1. **Install a JDK** (not just a JRE) — `javac` must be on PATH. Check with:
   ```
   javac -version
   ```
2. **Get the JUnit Console Launcher jar** and put it in `lib/` — see `lib/README.md` for
   the download link. Only keep one jar in that folder.
3. **Try the demo** (no setup needed, uses the bundled `examples/` folder):
   ```
   python grade.py --submissions examples/submissions --tests examples/tests --out examples/grades.csv --scores-out examples/scores.csv
   ```
   You should see 5 example students graded — one compile error, an all-pass folder
   submission, an all-pass zip (which also bundled its own copy of the test file, correctly
   skipped in favor of the official one), a partial-pass single `.java` file, and a
   partial-pass `.jar` — with `examples/grades.csv` written out.

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

If a student's row looks wrong, rerun just that one case with `--keep-build` and look in
`build_tmp/<student_id>/` — you'll find exactly what got compiled (student files with any
skipped/colliding ones noted, plus the official test files) and, if it compiled, the
`.class` files in `build_tmp/<student_id>/classes/`.

## Known behavior / edge cases handled

- **Compile errors** → score 0, the javac error is saved in `notes` so you can see why at
  a glance (student's own build path is stripped out of the message for readability).
- **Infinite loops / hangs** → killed after `--timeout` seconds (default 30), scored 0
  with a note, rather than hanging the whole grading run. This is enforced with a hard
  process-tree kill, not just Python's `subprocess` timeout, which isn't reliable against
  a silently-hung JVM on Windows.
- **Skipped tests** (e.g. `@Disabled`) are not counted in `tests_total`; noted in `notes`.
- **0 tests found** (student renamed/overwrote a class the test depends on) is reported
  distinctly from a compile failure.
- **A single-file submission renamed to the student's ID** by the LMS (e.g. `87654321.java`
  containing `public class Calculator`) is automatically re-named to `Calculator.java`
  when compiled, since Java requires a public type's filename to match its name.
- **Corrupt/empty zip or jar files** are reported in `notes` rather than silently skipped.
- One student's broken submission can never crash the whole run — every per-student step
  is wrapped so a batch of 60 always finishes even if one submission is garbage.

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
  examples/                <- committed demo data, safe to share (not real students)
    submissions/
    tests/
  build_tmp/               <- gitignored scratch space, created/cleaned automatically
```

## Ideas for extending

- Partial credit per test instead of flat pass/fail count
- Parse JUnit's XML report instead of the text summary (add `--reports-dir` to the launcher
  command) for more robust parsing
- A `config.yaml` per week (test folder, timeout, weight) instead of CLI flags
- Basic plagiarism screening (MOSS or a diff-based similarity pass) before grading
- Parallelize student runs with a process pool (keep timeouts — don't let one infinite loop
  block the others)
