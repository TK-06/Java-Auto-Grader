# Private test archive

## Problem

`Java-Auto-Grader` (`TK-06/Java-Auto-Grader` on GitHub) is a **public** repo.
`tests/` and `submissions/` are gitignored specifically so official test
files, `rubric.json` (the answer key's point values), and real student work
never end up in that public history - confirmed clean today: only empty
`.gitkeep` placeholders have ever been committed there.

That means every week's official tests currently exist in exactly one
place: the TA's local `tests/` working copy, sourced ad hoc from whatever
lab-materials folder was downloaded that week. Nothing about a week's setup
is versioned, backed up, sharable with another TA, or reusable if the same
assignment comes back next term - each week starts from the raw lab docx
again from scratch.

## Goals

- Version and back up each week+question's official tests, `rubric.json`,
  and `structure.json`, in a place that can never be exposed to students.
- Let it be reused next term (pull last time's Week 3 setup back down)
  without redoing the docx-extraction work in `setup-week`.
- Let it be shared with other TAs grading the same course this term.
- Never require re-cloning or touching the public repo to get the latest
  `grade.py` - the archive only ever holds `tests/` content, so pulling a
  week's tests can never roll back or otherwise affect which `grade.py` a
  TA is running.

## Non-goals

- **Automatic/live sync** (git submodule, sparse-checkout, CI). Explicitly
  ruled out during design in favor of a manual clone-and-copy step, matching
  how `tests/` already gets populated today (a deliberate per-week TA
  action, not something that happens automatically).
- **Per-term duplication of each week's folder.** History of how a given
  week's rubric changed across terms lives in git log for that path, not in
  parallel `2026-1/week01` vs `2026-2/week01` folders.
- **Granting different people access to different weeks.** One repo, one
  access list, covers the whole course - addressed here only as "TAs can be
  added as collaborators later," not designed around per-week ACLs.

## Design

### A second, private repo: `TA-test-grading-setup`

Created via `gh repo create TA-test-grading-setup --private` under the
`TK-06` account. Entirely separate from `Java-Auto-Grader` - no submodule
link, no shared history - so the public repo's visibility can never leak
into it and vice versa.

Internal layout, flat by week and question, git history carrying how a
week's content changed over time rather than parallel per-term folders:

```
TA-test-grading-setup/
  week01/
    q1/
      tests/
        TestCPTSMachine2.java
        TestStation2.java
        TestTicket2.java
      rubric.json
      structure.json
    q2/
      tests/...
      rubric.json
      structure.json
  week02/
    q1/...
```

### Local clone location: sibling of `grading/`, never nested inside it

Cloned to `J_Unit_Auto_Grader/TA-test-grading-setup/` - a sibling of `grading/`, not
a subfolder of it. `tests/` inside `grading/` is already gitignored, so
nesting the archive clone there wouldn't leak it either, but keeping it
fully outside the public repo's working tree removes the possibility
entirely (no accidental `git add -A` inside `TA-test-grading-setup/` could ever
touch `grading`'s history, and vice versa).

### Two helper scripts, living in the private repo

Since automatic syncing is a non-goal, these just turn the manual
clone-and-copy into one command instead of several:

- `restore-week.sh weekNN qN` - copies
  `TA-test-grading-setup/weekNN/qN/{tests/*,rubric.json,structure.json}` into
  `grading/tests/`, replacing whatever's there (matching the "whatever was
  there from last week gets replaced" policy `README.md` already documents
  for `tests/`).
- `archive-week.sh weekNN qN` - the reverse: copies `grading/tests/*` into
  `TA-test-grading-setup/weekNN/qN/`, then `git add`/`commit`/`push` in the archive
  repo.

Both are plain shell scripts with no dependency beyond `cp`/`git`, callable
from either repo's root.

### `setup-week` skill gets one new step at the front

Before extracting anything from the lab docx, check whether
`TA-test-grading-setup/weekNN/qN/` already exists. If it does (this exact
week+question was graded in a past term), run `restore-week.sh` and skip
straight to reporting the rubric back - no docx parsing needed. If it
doesn't, proceed with today's extraction flow exactly as-is, and finish by
reminding the TA to run `archive-week.sh` to save this week's setup for
next time.

`grade.py` itself is never read, written, or referenced by any part of this
- its own versioning stays entirely inside the public `Java-Auto-Grader`
repo, untouched by which week's tests happen to be loaded locally.

### TA access

Private repos support collaborators from creation - `Settings →
Collaborators` on GitHub, or `gh repo add-collaborator TK-06/TA-test-grading-setup
<username>`. Not exercised as part of this setup (no TA usernames given
yet); left for the repo owner to do whenever another TA needs access.

### Error handling

- `restore-week.sh`/`archive-week.sh` on a `weekNN`/`qN` pair that doesn't
  exist yet on the relevant side: fail loudly with the path it looked for,
  rather than silently creating an empty/partial folder.
- `archive-week.sh` never force-pushes or overwrites another TA's
  already-pushed commit for the same week - a normal `git push` is enough;
  a rejected push (someone else archived first) surfaces as a normal git
  error for the TA to resolve (pull, merge, re-push), not something the
  script papers over.

### Testing

No existing automated test suite covers repo/script setup like this (that's
what `test_grade.py` covers - grading logic itself, untouched here). Verification
is manual: create the repo, run `archive-week.sh week01 q1` against the
setup already sitting in `grading/tests/` from this week, confirm it lands
correctly in the archive, then run `restore-week.sh week01 q1` into a
scratch copy of `tests/` and diff it back against the original to confirm a
round trip is lossless.
