# DSA Grading - VS Code extension

The weekly grading runbook as a guided checklist inside VS Code. Each step:

- says what to do and why;
- shows what disk says about it right now;
- takes its inputs in a form;
- runs its script with a **Run** button, with live progress and the full output in VS Code's
  terminal;
- has a prompt you can copy into Claude Code (or any agent) in the terminal beside it.

It runs the same scripts the README documents (`extract_submissions.py`, `grade.py`,
`check_lateness.py`, `build_report.py`, the audits, `restore-week.sh`). There is nothing it does
that the command line can't. It just remembers the order, the flags and the inputs for you.

**It never runs git.** `archive-week.sh` commits and pushes, so it only appears as a command to
copy.

## Install

```bash
cd vscode-extension
npm ci
npm run package
code --install-extension dsa-grading-7.0.2.vsix
```

Rebuild and reinstall the same way after pulling a change to the extension.

## First run

1. Open the folder that holds `grading/` (for example `java-auto-grader/`) in VS Code. The
   extension wakes up in any workspace containing `grade.py`. If you opened a parent folder
   instead (all of your projects, say), it searches the workspace for the grader: a folder
   holding `grade.py` beside `tests/` and `lib/`. If it finds one, it uses it. If it finds
   several, it asks which. **Choose grader folder…** (the button on the "can't find the grader"
   panel, the `…` menu of the checklist, or the command palette) switches by hand. The choice is
   remembered for that workspace without writing a settings file.
2. The current step's page opens in the editor. The checklist is the **DSA Grading** icon in the
   activity bar.
3. Accept **Move it** on the first-run prompt, and pick *Secondary Side Bar*, to put the checklist
   on the right. You can also run *DSA Grading: Move the checklist to the right side bar* any time,
   or just drag the icon there. VS Code remembers the placement. (Extensions can't place a view on
   the right by default, which is why this takes one step.)

A desktop shortcut that runs `code "<path to java-auto-grader>"` makes this one double-click away.

## The steps

| # | Step | Runs |
|---|---|---|
| 1 | Get the week's materials | - (prompt: read the marking guide, find the hand-graded part) |
| 2 | Load the week's setup | `restore-week.sh weekNN qN` (asks first if `tests/` is loaded); `archive-week.sh` is copy-only |
| 3 | Get the submissions | - (pick the export zips; remembered for steps 4 and 7) |
| 4 | Extract | `extract_submissions.py <zips> --question N` (plus a dry run) |
| 5 | Run the grader | `grade.py`, after checking `javac` is 25, with a per-student progress bar |
| 6 | Validate | counts of every flag in `grades.csv`; `dupe_check.py`, `audit_given_code.py`, `audit_forbidden.py` |
| 7 | Late penalties | `check_lateness.py <zips> --deadline <picked> --question N`. The deadline is formatted for you |
| 8 | Upload | - (reveal `mcvScore.csv`) |
| 9 | Report | `build_report.py`, then open `report.html` |
| 10 | Hand-grading | - (prompt for your assigned students) |

A step is ticked automatically when disk shows it's done. For example, step 5 is done when
`grades.csv` is newer than everything in `submissions/`. A step with nothing to detect is ticked by
hand. Ticks and inputs are kept per workspace. **New week…** clears them, and never touches a file.

## Settings

| Setting | Default |
|---|---|
| `dsaGrading.gradingDir` | auto: a workspace folder holding `grade.py` or its `grading/` child, else a workspace search (see *First run*) |
| `dsaGrading.setupRepoDir` | `../TA-grading-setup` next to the grader |
| `dsaGrading.materialsDir` | empty. The professor's materials folder, for step 1's *Open* button |
| `dsaGrading.python` | `python` |
| `dsaGrading.bash` | Git for Windows' `bash.exe` if installed, else `bash` |
| `dsaGrading.openOnStartup` | `true` |

## Developing

```bash
npm ci
npm test            # compiles, then runs test/*.test.ts with node:test
```

Press F5 from this folder in VS Code to try it in an Extension Development Host. The runbook
itself is data in `src/steps.ts`. A new step or flag goes there, together with a test in
`test/steps.test.ts`. `src/steps.ts`, `src/state.ts` and `src/progress.ts` don't import `vscode`,
so they're tested with plain node. Everything that talks to VS Code is in `controller.ts`,
`runner.ts`, `stepPanel.ts` and `stepsView.ts`.

The privacy rule in the repo's [CONTRIBUTING.md](../CONTRIBUTING.md) applies here too: nothing
week-specific and no student IDs in this folder. The extension only reads them from the TA's own
`tests/`, `submissions/` and `results/` at runtime.
