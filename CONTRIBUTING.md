# Contributing

Everything you need to change the auto-grader without changing anyone's score by accident.

Read the **[README](README.md)** first. It documents every grading policy the tool enforces, and
a change that contradicts it is wrong until the README says otherwise.

> **Students:** if you think your submission was graded wrongly, raise it with your TA. That's a
> grading question, not an issue or a pull request. If you found a way to cheat the grader, see
> private material, or harm the machine running it, report it privately as described in
> **[SECURITY.md](SECURITY.md)**.

---

## 1. Setup

```bash
git clone https://github.com/TK-06/Java-Auto-Grader.git
cd Java-Auto-Grader
python -m pip install pytest
javac -version       # must print 25
```

| What | Version | Notes |
|---|---|---|
| Python | 3.13 | The tools use the **standard library only**, so nothing to `pip install` to grade |
| JDK | 25 | A JDK, not a JRE. It's the grading environment's requirement, see the README's **One-time setup** |
| pytest | 9.x | Only for the test suites. CI pins `9.1.1` |
| JUnit Console Launcher | committed in `lib/` | See [lib/README.md](lib/README.md) only if you're bumping it |
| Node.js | 24 | Only for the VS Code extension in `vscode-extension/`. CI uses 24 |

There's nothing to configure. A fresh clone runs the test suites as is. To grade for real you also
need a week's official tests in `tests/` and submissions in `submissions/`, both gitignored. See
the README's **Weekly workflow**.

---

## 2. Before you push: all of this must pass

```bash
python -m pytest -q
```

This runs all three suites (`test_grade.py`, `test_audit_given_code.py`, `test_check_lateness.py`).
CI runs exactly this on Windows with JDK 25, plus a privacy guard (§4, rule 1). Many tests compile
and run real Java, so `javac` must be on PATH. Three tests cover Windows read-only folders and are
skipped elsewhere.

If you touched `vscode-extension/`, also run, from that folder:

```bash
npm ci
npm test
npm run package
```

`npm test` includes a check that every note marker the extension counts in step 6 still appears in
`grade.py` or `check_lateness.py`. A rewording in either script fails CI here rather than quietly
zeroing a count. **Changing a marker string in a script means updating `FLAGS` in
`vscode-extension/src/state.ts` in the same commit.**

If you touched `grade.py`, `build_report.py` or a week's test setup, the unit suites are only
step 1 of 4. Also run the other three from the README's **Verifying a change before it ships**:

2. **TA side:** the reference solution scores full marks and the untouched starter a clean 0.
3. **Student side:** `verify_student_run.py` gives every student the same score `grade.py` did.
4. **Re-grade the cohort and diff:** a behaviour-preserving change gives identical scores. An
   intentional one changes only the rows you named in advance.

**Every verification run writes to scratch paths** (`--submissions`, `--out`, `--scores-out`
pointed outside the repo). That's a hard rule. A run into the real `results/` regenerates
`grades.csv` from scratch and silently drops any hand-applied TA overrides in it.

---

## 3. Where things live

| File | Job |
|---|---|
| `grade.py` | The grader: compiles each submission with the official tests, runs JUnit, applies caps, writes `results/grades.csv` + `mcvScore.csv` |
| `extract_submissions.py` | MyCourseVille bulk export → one `submissions/<studentID>.<ext>` per student |
| `check_lateness.py` | Real submission times from the original export zips → late caps baked into `mcvScore.csv` |
| `build_report.py` | The student-facing class report. Generic engine only, week data comes from `tests/report_config.json` |
| `verify_student_run.py` | Rebuilds each student's own project and runs the tests the way their IDE would |
| `audit_forbidden.py`, `dupe_check.py`, `audit_given_code.py` | Read-only audits. Evidence for a human, never a score |
| `test_*.py` | The unit suites |
| `lib/` | The committed JUnit Console Launcher jar |
| `vscode-extension/` | Optional VS Code extension: the weekly workflow as a checklist with Run buttons. The runbook is data in `src/steps.ts`. It never runs git |
| `submissions/`, `tests/`, `results/`, `scratchPad/` | Gitignored. Real student work, answer keys and scores live here and **only** here |

---

## 4. Rules

These aren't style preferences. Breaking them costs real students real marks, or leaks their data.

1. **Nothing private is ever committed. This repository is public.** That covers student work,
   scores, IDs, and answer-key material: official tests, `rubric.json`, `structure.json`,
   `manual_review.json`, `starter/`, `report_config.json`. `.gitignore` only protects the paths it
   names. A backup folder you create *inside* the repo is not ignored, and the next `git add -A`
   publishes it. This has happened once, with a whole class's `grades.csv`. Scratch and backup
   copies go **outside the repository**. Code, tests and docs use synthetic IDs (`10000001`,
   `1000000001`) and invented class names (`Item`, `Tree`). A pushed commit stays in history even
   after you delete the file. CI's `privacy-guard` job fails any tracked file under those folders,
   or any `.csv` `.zip` `.html` `.bak` `.java` `.class` `.docx` `.pdf`, or a stray `.jar`. That's a
   safety net, not permission to rely on it.
2. **Standard library only.** A TA must be able to grade on a fresh machine with Python and a JDK.
   **Discuss before adding any third-party dependency.**
3. **`grade.py` owns every marker string; `build_report.py` imports it.** For example,
   `WRONG_SUBMISSION_PREFIX`. Re-spelling one as a literal in the report drifts out of sync
   silently the next time the wording changes.
4. **A new kind of 0, cap or flag isn't finished when `grades.csv` records it.** It reaches the
   student through `build_report.py`, and a note the report doesn't recognise is dumped raw. A new
   category needs four touch points, listed in `build_report.py`'s module docstring:
   `category_of()`, `CATEGORY_LABEL`, `CATEGORY_MEANING`, `_render_category_reason()`.
5. **Write everything a student can see for the student.** Say what happened, then the evidence,
   then what to do about it, with the hint matching the actual cause. Never show a number they
   can't act on, or raw compiler output without a sentence explaining it.
6. **`TA OVERRIDE:` is reserved for an actual score override.** `check_lateness.py` keys off that
   exact string to skip a row, so using it as a general prefix silently exempts that student from
   late penalties. Use `TA NOTE:` for an annotation that leaves the score alone.
7. **Never weaken or delete a test to make a change pass.** A red test is a regression until
   proven otherwise. A new behaviour comes with a new test.
8. **Update the README in the same commit.** A change to grading behaviour updates the README's
   **Grading policy** or the matching section. The README must never lag the code.
9. **Match the code around you.** Comments here explain *why*, usually the real submission that
   made it necessary, and they're long on purpose. Don't reformat or refactor what your change
   doesn't need.

---

## 5. Branching, commits, versions

One feature or fix at a time, each on its own branch, as one commit. It's merged onto `main` with
a merge commit and tagged with its version.

1. **Branch** named after the thing being built: `feat/<thing>` or `fix/<thing>`.
2. **Pick the version.** The repository has no version file. The version lives in the commit
   message and the tag.
   - A fix bumps the patch: 6.2.0 → 6.2.1.
   - A feature, new detection or new cap bumps the minor: 6.2.0 → 6.3.0.
   - A change to how grading works as a whole, or to a config file's format, bumps the major:
     6.2.0 → 7.0.0.
3. **Commit** as `v<version> feat: <what>` or `v<version> fix: <what>`, in plain words. If the
   change can move anyone's score, say so in the commit body: whose, and why.
4. **Merge** onto `main` with a merge commit, never a fast-forward, so each change stays one
   visible unit in the history.
5. **Tag** the merge commit `v<version>` (annotated).
6. **Push** `main` with its tags. CI runs on every push to `main`. Only push what passed §2.

```bash
git switch -c feat/report-legend
# ...work, then everything in §2...
git add -A
git status                      # read it: nothing from submissions/, results/ or tests/
git commit -m "v6.3.0 feat: legend for every report category"
git switch main
git merge --no-ff feat/report-legend -m "Merge feat/report-legend (v6.3.0)"
git tag -a v6.3.0 -m "v6.3.0: legend for every report category"
git push origin main --follow-tags
git branch -d feat/report-legend
```

**If you're not the repository owner,** push your branch and open a pull request into `main`
instead of merging it yourself. Every pull request needs CI green and an approving review from
@TK-06 (`.github/CODEOWNERS`). In the description, say whether the change can move any score, and
which of the four §2 checks you ran.

---

## 6. Gotchas that have actually bitten us

- **OneDrive marks every folder it syncs read-only,** about 20-35 s after the folder is created.
  Windows then refuses to delete it: `[WinError 5] Access is denied` on an *empty* folder. That
  isn't a locked file. `grade.py` clears the attribute and retries (`_clear_readonly_and_retry`).
  Any new code that deletes a folder it created must go through the same path. `attrib <folder>`
  shows whether `R` is set.
- **Windows has no time-zone database.** `ZoneInfo("Asia/Bangkok")` only works there when the
  third-party `tzdata` package happens to be installed, so it passed on one TA machine and crashed
  `check_lateness.py` (and `extract_submissions.py`, which imports it) on CI. `bangkok_tz()` falls
  back to a fixed UTC+7, which is exact because Thailand has no daylight saving. Test zone-dependent
  code with `python -s`, which hides user-installed packages like `tzdata`.
- **Windows' 260-character path limit.** Extracting a bulk export with its original
  `<studentID>/<very long filename>` structure fails with `FileNotFoundError`. Extract to short
  synthetic names and keep the real name only in memory.
- **`javac` output is flattened to `" | "`** by `truncate()` for the CSV. Anything that displays a
  compile error should `.replace(" | ", "\n")` first.
- **`has been compiled by a more recent version of the Java Runtime`** in a row is a student's
  `.class` built with a newer JDK. That's a real, handled case, not a grader bug. JDK 25's `javap`
  refuses those files too, so use `grade.class_file_methods` to read their method table.
- **Two submitted files with the same class name:** `grade.py` keeps the path that sorts first
  alphabetically, which isn't necessarily the student's latest copy. If a working-looking
  submission fails to compile, check for a second project copy inside the archive.
- **`package X does not exist` for a package the submission itself declares** is a keep-set
  question before it's the student's bug. Reproduce it with a one-student scratch run before
  ruling on it.
- **`check_lateness.py --deadline` takes `YYYY-MM-DDTHH:MM:SS`,** with nothing between the date and
  the `T`. `2026-09-06:T23:59:59` fails with `Invalid isoformat string`. A naive time is read as
  Asia/Bangkok.
- **A hand-written note typed into `grades.csv` lasts only until the next re-grade.** Put
  student-facing TA notes in `report_config.json`'s `ta_notes`, byte-identical to any copy in the
  CSV, or the report prints them twice.

---

## 7. Grading invariants

These are what students and TAs rely on. A change that breaks one is wrong, however well tested.

- **The official test always wins.** It overwrites any copy of the same file a student bundled,
  so no submission can change what it's tested against.
- **`grades.csv` keeps the technical truth.** Under a TA override, `compiled`, `tests_passed`,
  `passed_tests` and `uncapped_score` still say what really happened. Only `score`, `score_cap`
  and the `TA OVERRIDE:` note carry the decision, so an appeal can see the code genuinely worked.
- **Caps from `grade.py` multiply** (50% × 90% = 45%). **The late penalty is a ceiling compared
  with them, never multiplied:** the stricter one wins. A student scoring below their late ceiling
  loses nothing.
- **`grades.csv` is never modified after grading.** `check_lateness.py` only reads it and rewrites
  `mcvScore.csv`, the file that gets uploaded. Re-running it with the same deadline is always safe.
- **The report shows the score the student was actually given,** or says why it differs, for
  example a late penalty.
- **An audit never changes a score by itself.** Duplicate checks, forbidden-method audits and
  `MANUAL REVIEW` flags are evidence for a human, who decides and writes a `TA OVERRIDE:`.
