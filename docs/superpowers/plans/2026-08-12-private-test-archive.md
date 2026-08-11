# Private Test Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a private GitHub repo that archives each week+question's official tests, `rubric.json`, and `structure.json`, with two scripts to move content between it and the public grading repo's `tests/` working copy.

**Architecture:** A brand-new, standalone private repo (`TK-06/Java-Auto-Grader-Tests`), cloned locally as a sibling of `grading/` (never nested inside it). Two plain bash scripts inside that repo (`archive-week.sh`, `restore-week.sh`) do a one-way `cp` + (for archive) `git commit`/`push` between `grading/tests/` and `<archive>/weekNN/qN/`. No submodule, no CI, no automatic syncing — every transfer is a deliberate, manually-run command.

**Tech Stack:** `gh` CLI (repo creation), `git`, POSIX `bash` (Git Bash on this machine) — no new runtime dependency, nothing added to `grade.py` or its Python test suite.

## Global Constraints

- Repo name: `Java-Auto-Grader-Tests`, owner `TK-06`, visibility **private**.
- Local clone path: sibling of `grading/`, i.e.
  `C:\Users\Palan\OneDrive\Documents\computer_programming\Projects\J_Unit_Auto_Grader\grading-tests`
  — never a subfolder of `grading/`.
- Archive layout is flat by week+question:
  `weekNN/qN/{tests/*.java, rubric.json, structure.json}` — no per-term duplication; git history is how a week's content across terms is inspected.
- No automatic/live sync (no submodule, no sparse-checkout, no CI). Every transfer is a manually-run script.
- `archive-week.sh` never force-pushes; a rejected push surfaces as a normal git error, not something the script papers over.
- `grade.py` (and the rest of the public `Java-Auto-Grader` repo) is never read or written by anything in this plan.

---

### Task 1: Create the private archive repo and scaffold it

**Files:**
- Create (new repo root): `Java-Auto-Grader-Tests/README.md`
- Create (new repo root): `Java-Auto-Grader-Tests/.gitattributes`

**Interfaces:**
- Produces: a private GitHub repo `TK-06/Java-Auto-Grader-Tests`, cloned locally at
  `C:\Users\Palan\OneDrive\Documents\computer_programming\Projects\J_Unit_Auto_Grader\grading-tests`
  — this exact path is what Tasks 2–4 assume as `$SCRIPT_DIR`'s parent.

- [ ] **Step 1: Create the private repo on GitHub**

Run:
```
gh repo create TK-06/Java-Auto-Grader-Tests --private --description "Private archive of official weekly tests/rubrics for TK-06/Java-Auto-Grader (never merge into the public repo)"
```
Expected: prints the new repo's URL (`https://github.com/TK-06/Java-Auto-Grader-Tests`), exit code 0.

- [ ] **Step 2: Clone it locally as a sibling of `grading/`**

Run:
```
git clone https://github.com/TK-06/Java-Auto-Grader-Tests.git "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests"
```
Expected: "warning: You appear to have cloned an empty repository" (fine — nothing pushed yet), and the directory exists.

Verify: `ls "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/"` shows both `grading` and `grading-tests` as siblings.

- [ ] **Step 3: Add `.gitattributes` forcing LF line endings for the shell scripts**

File: `C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests/.gitattributes`
```
*.sh text eol=lf
```
This matters because Tasks 2–3 write `#!/usr/bin/env bash` scripts on a Windows checkout — without it, Git could check them out with CRLF line endings and break the shebang.

- [ ] **Step 4: Add the repo README documenting the layout and script usage**

File: `C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests/README.md`
```markdown
# Java-Auto-Grader-Tests

Private archive of each week's official JUnit tests, `rubric.json`, and
`structure.json` for the Data Structures auto-grader
([TK-06/Java-Auto-Grader](https://github.com/TK-06/Java-Auto-Grader), public).
Never merge or link this into that public repo — it holds answer-key
content that must stay hidden from students.

## Layout

    weekNN/
      qN/
        tests/            <- official test .java files (never the student-facing versions)
        rubric.json
        structure.json    <- omitted if that week didn't use one

Flat by week+question; git history carries how a week's rubric changed
across terms rather than duplicating folders per term.

## Usage

Assumes this repo is cloned as a sibling folder of `grading/` (i.e.
`../grading-tests` relative to `grading/`'s own parent folder):

    ./restore-week.sh week01 q1   # archive -> grading/tests/
    ./archive-week.sh week01 q1   # grading/tests/ -> archive, commit, push

Both accept an optional 3rd argument — the path to the grading repo — if
it isn't a sibling folder literally named `grading`.

## Adding another TA

`gh repo add-collaborator TK-06/Java-Auto-Grader-Tests <username>`, or
GitHub → repo → Settings → Collaborators.
```

- [ ] **Step 5: Commit and push the scaffold**

```bash
cd "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests"
git add README.md .gitattributes
git commit -m "scaffold: repo layout and usage docs"
git push -u origin main
```
Expected: push succeeds (this is the first commit, so it also sets `main` as the default branch upstream).

---

### Task 2: `archive-week.sh` — copy `grading/tests/` into the archive, commit, push

**Files:**
- Create: `grading-tests/archive-week.sh`

**Interfaces:**
- Consumes: nothing from another task's code (reads `grading/tests/*.java`, `rubric.json`, `structure.json` directly off disk).
- Produces: `archive-week.sh <weekNN> <qN> [grading-dir]` — CLI contract Task 4's `setup-week` skill update will tell the TA to run. Exit 0 on success (including the "nothing changed" no-op case) or on the "already found `grading/tests/` before" no-op case; exit 1 with a message on `stderr` if `<grading-dir>/tests/` doesn't exist or has no `.java` files directly in it.

- [ ] **Step 1: Write the script**

File: `C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests/archive-week.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <weekNN> <qN> [path-to-grading-repo]" >&2
    exit 1
fi

WEEK="$1"
Q="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRADING_DIR="${3:-$SCRIPT_DIR/../grading}"

SRC="$GRADING_DIR/tests"
DEST="$SCRIPT_DIR/$WEEK/$Q"

if [ ! -d "$SRC" ]; then
    echo "error: tests/ dir not found at $SRC (pass the grading repo's path as a 3rd argument)" >&2
    exit 1
fi

JAVA_COUNT=$(find "$SRC" -maxdepth 1 -name "*.java" | wc -l)
if [ "$JAVA_COUNT" -eq 0 ]; then
    echo "error: no .java files found directly in $SRC - nothing to archive" >&2
    exit 1
fi

mkdir -p "$DEST/tests"
find "$DEST/tests" -mindepth 1 -delete
cp "$SRC"/*.java "$DEST/tests/"
if [ -f "$SRC/rubric.json" ]; then
    cp "$SRC/rubric.json" "$DEST/rubric.json"
fi
if [ -f "$SRC/structure.json" ]; then
    cp "$SRC/structure.json" "$DEST/structure.json"
fi

cd "$SCRIPT_DIR"
git add "$WEEK/$Q"
if git diff --cached --quiet; then
    echo "no changes to archive for $WEEK/$Q"
    exit 0
fi
git commit -m "archive $WEEK/$Q"
git push

echo "archived $WEEK/$Q and pushed"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests/archive-week.sh"
```

- [ ] **Step 3: Verify the failure case — missing `tests/` dir**

Run:
```bash
cd "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests"
./archive-week.sh week99 q9 /nonexistent-path
```
Expected: exits non-zero, prints `error: tests/ dir not found at /nonexistent-path/tests ...` to stderr.

- [ ] **Step 4: Verify the failure case — empty `tests/` dir**

Run:
```bash
mkdir -p /tmp/empty-grading/tests
./archive-week.sh week99 q9 /tmp/empty-grading
```
Expected: exits non-zero, prints `error: no .java files found directly in /tmp/empty-grading/tests - nothing to archive`.

- [ ] **Step 5: Verify the success case against the real `grading/tests/`**

`grading/tests/` already holds this week's real Week 1 Q1 setup (`TestCPTSMachine2.java`, `TestStation2.java`, `TestTicket2.java`, `rubric.json`, `structure.json`) from earlier this session — use it as the live test case instead of synthetic fixtures.

Run:
```bash
cd "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests"
./archive-week.sh week01 q1
```
Expected: prints `archived week01/q1 and pushed`, exit 0.

Verify:
```bash
ls week01/q1/tests/
cat week01/q1/rubric.json
cat week01/q1/structure.json
git log --oneline -1
```
Expected: `tests/` has the 3 `.java` files, `rubric.json`/`structure.json` match what's in `grading/tests/`, and the latest commit is `archive week01/q1`.

- [ ] **Step 6: Verify the no-op case (nothing changed since last archive)**

Run: `./archive-week.sh week01 q1` again immediately.
Expected: prints `no changes to archive for week01/q1`, exit 0, no new commit (`git log --oneline -1` unchanged).

- [ ] **Step 7: Commit the script itself**

```bash
git add archive-week.sh
git commit -m "add archive-week.sh"
git push
```

---

### Task 3: `restore-week.sh` — copy the archive into `grading/tests/`

**Files:**
- Create: `grading-tests/restore-week.sh`

**Interfaces:**
- Consumes: the `weekNN/qN/` layout `archive-week.sh` (Task 2) produces — `tests/*.java`, `rubric.json`, optional `structure.json`.
- Produces: `restore-week.sh <weekNN> <qN> [grading-dir]` — the other half of the CLI contract Task 4 references. Exit 0 on success; exit 1 with a message on `stderr` if `<archive>/weekNN/qN/` or `<grading-dir>/tests/` doesn't exist.

- [ ] **Step 1: Write the script**

File: `C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests/restore-week.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <weekNN> <qN> [path-to-grading-repo]" >&2
    exit 1
fi

WEEK="$1"
Q="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRADING_DIR="${3:-$SCRIPT_DIR/../grading}"

SRC="$SCRIPT_DIR/$WEEK/$Q"
DEST="$GRADING_DIR/tests"

if [ ! -d "$SRC" ]; then
    echo "error: no archived setup at $SRC" >&2
    exit 1
fi
if [ ! -d "$DEST" ]; then
    echo "error: tests/ dir not found at $DEST (pass the grading repo's path as a 3rd argument)" >&2
    exit 1
fi

find "$DEST" -mindepth 1 -not -name ".gitkeep" -delete
cp -r "$SRC/tests/." "$DEST/"
cp "$SRC/rubric.json" "$DEST/rubric.json"
if [ -f "$SRC/structure.json" ]; then
    cp "$SRC/structure.json" "$DEST/structure.json"
fi

echo "restored $WEEK/$Q into $DEST"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests/restore-week.sh"
```

- [ ] **Step 3: Verify the failure case — week not archived**

Run:
```bash
cd "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests"
./restore-week.sh week99 q9
```
Expected: exits non-zero, prints `error: no archived setup at .../week99/q9`.

- [ ] **Step 4: Round-trip verification against a scratch copy (not the real `grading/tests/`)**

Restoring straight into the real `grading/tests/` wouldn't prove anything new — Task 2 archived it *from* there, so it already matches. Instead restore into an isolated scratch directory and diff against the archive to confirm the copy is lossless:

```bash
mkdir -p /tmp/scratch-grading/tests
./restore-week.sh week01 q1 /tmp/scratch-grading

diff -r /tmp/scratch-grading/tests/ week01/q1/tests/
diff /tmp/scratch-grading/tests/rubric.json week01/q1/rubric.json
diff /tmp/scratch-grading/tests/structure.json week01/q1/structure.json
```
Expected: `restored week01/q1 into /tmp/scratch-grading/tests`, and all three `diff` commands produce no output (identical).

- [ ] **Step 5: Verify old scratch content gets replaced, not merged**

```bash
touch /tmp/scratch-grading/tests/StaleLeftover.java
./restore-week.sh week01 q1 /tmp/scratch-grading
ls /tmp/scratch-grading/tests/
```
Expected: `StaleLeftover.java` is gone — only this week's files remain, matching the "whatever was there gets replaced" policy `README.md` already documents for `tests/`.

- [ ] **Step 6: Commit the script**

```bash
cd "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading-tests"
git add restore-week.sh
git commit -m "add restore-week.sh"
git push
```

---

### Task 4: Wire the archive into the `setup-week` skill

**Files:**
- Modify: `grading/.claude/commands/setup-week.md`

**Interfaces:**
- Consumes: `restore-week.sh <weekNN> <qN>` / `archive-week.sh <weekNN> <qN>` exactly as defined in Tasks 2–3 (same working directory assumption: archive repo cloned as sibling `../grading-tests` of `grading/`'s parent).

- [ ] **Step 1: Add a "check the archive first" step before today's step 1, and a reminder at the end**

Edit `grading/.claude/commands/setup-week.md`. Insert a new step 0 immediately after the opening two paragraphs (before the existing numbered "Locate the materials" step), renumber nothing else (the existing steps stay 1–6, this is a new step that comes before them so it reads naturally as step 0):

Old:
```markdown
Follow these steps:

1. **Locate the materials.**
```

New:
```markdown
Follow these steps:

0. **Check the private archive first.** If
   `../grading-tests/weekNN/qN/` exists (a sibling folder of this repo's own
   parent, e.g. `../grading-tests/week01/q1/` for Week 1 Q1 — ask the user
   for the week/question number if it's not obvious from the lab folder
   path) — this exact week+question was graded in a past term. Run
   `../grading-tests/restore-week.sh weekNN qN` and skip straight to step 6
   (report back) — no docx parsing needed. If that folder doesn't exist, or
   `../grading-tests/` itself doesn't exist, proceed with the extraction
   steps below exactly as before.

1. **Locate the materials.**
```

Then change the closing paragraph. Old:
```markdown
Stop there. Don't run `python grade.py` — the user runs that themselves. Don't `git add` or
`git commit` anything in this command either: `tests/` (test cases) and `submissions/`
(student jars) are both gitignored on purpose and must never be committed — see
`.gitignore` and the "Project layout" section of `README.md`.
```

New:
```markdown
Stop there. Don't run `python grade.py` — the user runs that themselves. Don't `git add` or
`git commit` anything in *this* repo either: `tests/` (test cases) and `submissions/`
(student jars) are both gitignored on purpose and must never be committed — see
`.gitignore` and the "Project layout" section of `README.md`.

If step 0 didn't already restore this week from the archive (i.e. this was a fresh
extraction from the lab docx), remind the user to run
`../grading-tests/archive-week.sh weekNN qN` afterward to save this week's setup for
reuse next term — but don't run it yourself unasked, since it commits and pushes to
the private archive repo.
```

- [ ] **Step 2: Read the file back and confirm both edits landed correctly**

Read `grading/.claude/commands/setup-week.md` in full and confirm: step 0 appears before step 1, steps 1–6 are otherwise untouched, and the closing paragraph has the new reminder appended after the existing "Stop there" text.

- [ ] **Step 3: Commit in the `grading` repo**

```bash
cd "C:/Users/Palan/OneDrive/Documents/computer_programming/Projects/J_Unit_Auto_Grader/grading"
git add .claude/commands/setup-week.md
git commit -m "setup-week: check the private test archive before re-extracting from the docx"
```
Do not push automatically — this repo's push step is a separate user decision each time, same as every other commit made this session.

---

## Self-Review Notes

- **Spec coverage:** private repo + privacy (Task 1) · flat weekNN/qN layout (Tasks 1–3) · sibling-not-nested local clone (Task 1) · manual clone-and-copy, no submodule/sparse-checkout (Tasks 2–3, explicitly plain `cp`) · two helper scripts (Tasks 2–3) · `setup-week` integration, restore-first / archive-reminder (Task 4) · TA collaborator access documented (Task 1's README) · error handling — fail loudly on missing paths (Tasks 2–3 step 3 in each) · no force-push (archive-week.sh uses plain `git push`) · `grade.py` untouched (no task references it) · manual round-trip verification (Task 3 step 4) — all covered.
- **Placeholder scan:** no TBD/TODO markers; every step has literal script/command content.
- **Type consistency:** both scripts agree on the CLI contract (`<weekNN> <qN> [grading-dir]`) and the on-disk layout (`weekNN/qN/{tests/,rubric.json,structure.json}`); Task 4's skill edit references that same contract and the same sibling-folder assumption established in Task 1.
