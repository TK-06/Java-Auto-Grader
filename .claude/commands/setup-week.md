---
description: Set up this week's official tests + rubric.json from the lab materials, ready to run grade.py
---

Set up this week's grading content in `tests/` from these lab materials: $ARGUMENTS

If no path was given, ask for the path to this week's lab folder (or directly to the
`*_HowToMark.docx` file) before doing anything else.

Follow these steps:

0. **Check the private archive first.** If
   `../TA-test-grading-setup/weekNN/qN/` exists (a sibling folder of this repo,
   e.g. `../TA-test-grading-setup/week01/q1/` for Week 1 Q1 — ask the user
   for the week/question number if it's not obvious from the lab folder
   path) — this exact week+question was graded in a past term. Run
   `../TA-test-grading-setup/restore-week.sh weekNN qN` and skip straight to step 6
   (report back) — no docx parsing needed. If that folder doesn't exist, or
   `../TA-test-grading-setup/` itself doesn't exist, proceed with the extraction
   steps below exactly as before.

1. **Locate the materials.** Given a lab folder (e.g. `.../Lab/Q3`), find:
   - `Solution/*_HowToMark.docx` — the marking scheme
   - `Solution/src/test/java/*.java` — all test files (both official and student-facing)
   - `toStudent/src/test/java/*.java` — the test files actually handed to students (if this
     folder doesn't exist, ask which test files in `Solution` are official vs student-facing
     rather than guessing)
   If given the docx path directly instead of a folder, derive the other two paths from its
   location the same way.

2. **Extract the rubric from the docx.** It's a zip; there's no need to ask permission to
   unzip it — extract `word/document.xml`, pull the `<w:t>` run text per paragraph, and read
   off the point values per test method. Cross-check the total against the docx's own stated
   total (e.g. "17 points, will be scaled down to 10").

3. **Identify the official test files** — the ones in `Solution/src/test/java` whose names
   do NOT appear in `toStudent/src/test/java` (e.g. `TestBot02.java` vs the student-facing
   `TestBot.java`). These are the ones actually run against student code — never copy the
   student-facing versions into `tests/`.

4. **Replace `tests/` contents**, per `README.md`'s stated policy ("whatever was there from
   last week gets replaced"): remove old `.java`/`rubric.json` from a previous week, copy in
   this week's official test file(s).

5. **Write `tests/rubric.json`** from the extracted point values, matching the existing
   format (class name → method name → points, no package, no `()`).

6. **Report back**: a table of the rubric (class / method / points, with subtotals and
   grand total matching the docx), and which files ended up in `tests/`.

Stop there. Don't run `python grade.py` — the user runs that themselves. Don't `git add` or
`git commit` anything in *this* repo either: `tests/` (test cases) and `submissions/`
(student jars) are both gitignored on purpose and must never be committed — see
`.gitignore` and the "Project layout" section of `README.md`.

If step 0 didn't already restore this week from the archive (i.e. this was a fresh
extraction from the lab docx), remind the user to run
`../TA-test-grading-setup/archive-week.sh weekNN qN` afterward to save this week's setup for
reuse next term — but don't run it yourself unasked, since it commits and pushes to
the private archive repo.
