# -*- coding: utf-8 -*-
"""Builds the single, shareable, searchable HTML grading-report artifact from
results/grades.csv - the class-wide page students search their own ID in.

This file is the REUSABLE ENGINE only - page structure, CSS design tokens,
JS, HTML generation. It is the project's house style for these reports and
should NOT be redesigned from scratch each week; tweak it, don't replace it.

Everything week-specific (test explanations, assignment title, point
breakdown, non-submitter student IDs, any note redactions) lives in
tests/report_config.json instead of in this script - that directory is
already gitignored (see .gitignore's `tests/*` rule), same as
tests/rubric.json and tests/structure.json, precisely BECAUSE it can contain
real student IDs and content derived from officially-private test files.
Never hardcode a real student ID or answer-key-derived content back into
this .py file - it lives in the public repo.

Each new week: write a fresh tests/report_config.json (see the JSON shape
loaded below), then run this file, then publish OUT_PATH with the Artifact
tool (title = a short name for that week's assignment topic, e.g. "Cell
Simulation Grading"; favicon = one emoji tied to the subject).

WRITE EVERY STUDENT-FACING STRING FOR THE STUDENT, NOT THE GRADER. This
page is the only explanation most of them get. Three rules this file has
already been bitten by, all fixed in W5Q2:
  1. A hint must be chosen from the ACTUAL reason, never hard-coded per
     branch. The capped block used to print the no-source advice to every
     capped student, including one capped at 0% for a stub-only submission,
     who was told to tick "include source" - advice unrelated to their 0.
  2. Never render a number a student cannot act on. max_score is the STRING
     "0" for a submission rejected before the rubric ran, which is truthy,
     so `row["max_score"] or AUTOMATED_POINTS` silently produced "0/0".
  3. Interpret machine output rather than dumping it - a javac wall needs a
     sentence naming the likely cause above it.
A compile failure against a FIXED official test is diagnosable, not just
reportable: javac naming a method the TEST calls as `cannot find symbol`
means the submitted class is not this question's. analyze_compile_error()
extracts those, category_of() routes them to "wrongwork" (Wrong submission)
rather than "compile", and the reason block leads with the missing method,
with the raw javac collapsed behind a toggle. Signature-mismatch evidence
alongside it separates "you exported a different project" from "you never
wrote the method". Lab-agnostic - it reads javac, not the assignment - and
covers instance, static and interface call sites. Missing CLASSES are a
different case, already caught upstream by grade.py's structure.json check.

Category labels are grader vocabulary, so the page carries a legend defining
each one it shows. And a score shown here must reconcile with the score the
student actually received: grades.csv is the PRE-late-penalty record, which
is what late_penalties in report_config.json exists to explain.
"""
import csv, html, json, re, statistics
from pathlib import Path

CSV_PATH = Path(__file__).parent / "results" / "grades.csv"
OUT_PATH = Path(__file__).parent / "results" / "report.html"
CONFIG_PATH = Path(__file__).parent / "tests" / "report_config.json"

_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
ASSIGNMENT_EYEBROW = _config["assignment_eyebrow"]
ASSIGNMENT_TITLE = _config["assignment_title"]
AUTOMATED_POINTS = _config["automated_points"]
TOTAL_POINTS = _config.get("total_points")
MANUAL_COMPONENT_DESC = _config.get("manual_component_desc")
NOT_SUBMITTED = _config.get("not_submitted", [])
NO_VALID_Q2 = _config.get("no_valid_q2", [])
NOTE_REDACTIONS = _config.get("note_redactions", {})
# Optional {student_id: {"days": <int>, "uploaded_score": <str|num>}}. grades.csv is
# deliberately the PRE-late-penalty technical record (check_lateness.py only ever
# rewrites mcvScore.csv), so without this the report would show a late student the
# score they earned and the gradebook would show them a smaller one, with nothing
# explaining the gap. Generic and reusable: no IDs or week-specific text live here.
LATE_PENALTIES = _config.get("late_penalties", {})
# JSON gives [class_name, points, [[method, desc], ...]] - equivalent to the
# (str, int, [(str, str), ...]) shape the rest of this file expects; plain
# list unpacking works the same as tuple unpacking, so nothing else changes.
TEST_ORDER = _config["test_order"]

# ============================================================
# REUSABLE ENGINE - the project's house style; tweak, don't replace
# ============================================================

CLASS_POINTS = {c: p for c, p, _ in TEST_ORDER}


def esc(s):
    return html.escape(s or "", quote=True)


def parse_pairs(field):
    out = []
    for part in (field or "").split(";"):
        part = part.strip()
        if part and "." in part:
            cls, method = part.rsplit(".", 1)
            out.append((cls, method))
    return out


def parse_failure_details(field):
    out = {}
    for part in (field or "").split("; "):
        part = part.strip()
        if part and ":" in part:
            left, msg = part.split(":", 1)
            left = left.strip()
            if "." in left:
                cls, method = left.rsplit(".", 1)
                out[(cls, method)] = msg.strip()
    return out


def category_of(row):
    notes = row["notes"]
    if "TA OVERRIDE" in notes:
        return "override"
    if "timed out" in notes and row["tests_total"] in ("", "0"):
        return "timeout"
    if row["compiled"] == "no" and "STRUCTURE ERROR" in notes:
        return "structure"
    if row["compiled"] == "no" and "COMPILE ERROR" in notes:
        # Distinguish "your own code has a bug" from "this isn't the assignment" -
        # they need completely different explanations, and lumping the second under
        # "Compile error" buries the only fact that matters under a wall of javac.
        missing, _ = analyze_compile_error(extract_compile_error(notes))
        return "wrongwork" if missing else "compile"
    if row["compiled"] == "no":
        return "nosource"
    if row["score_cap"]:
        return "capped"
    if "PENALTY applied (rubric)" in notes:
        return "penalty"
    if row["tests_passed"] == row["tests_total"] and row["tests_total"] != "0":
        return "pass"
    # Earning every available point IS a full pass, even when some test the
    # student "failed" carries no marks - a rubric entry weighted 0 is an
    # informational check (see grade.py's rubric handling), so counting it in
    # tests_passed/tests_total must not label a full-marks row "Partial".
    try:
        if float(row["score"]) == float(row["max_score"]) and float(row["max_score"]) > 0:
            return "pass"
    except ValueError:
        pass
    return "partial"


MISSING_METHOD_RE = re.compile(
    r"symbol:\s*method\s+(\w+)\([^)]*\)\s*\n\s*location:\s*"\
    r"(?:variable\s+\w+\s+of type|class|interface)\s+([\w.]+)")
# javac saying the student's type is a DIFFERENT SHAPE from the one the official
# test compiles against - not merely missing a method.
VARIANT_EVIDENCE = (
    "is not compatible with",
    "does not override or implement a method from a supertype",
    "lossy conversion",
    "cannot implement",
)


def analyze_compile_error(err):
    """A compile failure against a FIXED official test says something specific:
    the test is known-good, so an error reporting that a method the test calls
    does not exist means the submitted class is not the class this question
    asked for. Returns (missing, is_variant): `missing` is the de-duplicated
    ["CDLinkedList.swapRange", ...] the test called and could not find, in the
    order javac first reported them; `is_variant` is True when javac ALSO
    complained that existing members have different types (e.g. a class storing
    char where this question's test passes int), which distinguishes "you
    exported a different assignment" from "you left the method unwritten"."""
    missing, seen = [], set()
    for method, owner in MISSING_METHOD_RE.findall(err or ""):
        key = f"{owner}.{method}"
        if key not in seen:
            seen.add(key)
            missing.append(key)
    return missing, any(sig in (err or "") for sig in VARIANT_EVIDENCE)


CATEGORY_LABEL = {
    "pass": "Full pass", "partial": "Partial", "capped": "Capped",
    "compile": "Compile error", "structure": "Structure error",
    "wrongwork": "Wrong submission",
    "nosource": "No source", "override": "TA review", "missing": "No submission",
    "timeout": "Timed out", "penalty": "Penalty",
}


def extract_compile_error(notes):
    # grade.py's truncate() flattens real multi-line javac output to " | "-joined
    # text for the CSV; restore real line breaks so it reads like actual compiler output.
    m = re.search(r"COMPILE ERROR:\s*(.*)$", notes)
    return m.group(1).replace(" | ", "\n") if m else ""


def render_late_notice(row):
    """Rendered ABOVE the category reason, for any student listed in
    late_penalties - including one who also compiled fine and passed
    everything, whose category reason is empty. Additive, never a
    replacement: a late submission that was also capped shows both."""
    info = LATE_PENALTIES.get(row["student_id"])
    if not info:
        return ""
    days = str(info.get("days", ""))
    uploaded = info.get("uploaded_score")
    day_word = "day" if days == "1" else "days"
    uploaded_html = ""
    if uploaded is not None:
        uploaded_html = (f' The score actually uploaded to MyCourseVille is '
                         f'<strong>{esc(str(uploaded))}/{AUTOMATED_POINTS}</strong>.')
    return f"""<div class="reason reason-warn">
      <p><strong>Submitted {esc(days)} {day_word} late.</strong> The score shown above is the technical
      test result, before the late penalty.{uploaded_html}</p>
      <p class="hint">Course policy: 10% off per day late, any partial day counting as a full day. This
      deduction is for submission time only - it says nothing about the code, and the test results below
      are unaffected by it.</p>
    </div>"""


def render_reason(row, cat):
    notes = NOTE_REDACTIONS.get(row["student_id"], row["notes"])
    return render_late_notice(row) + _render_category_reason(row, cat, notes)


def _render_category_reason(row, cat, notes):
    if cat in ("compile", "wrongwork"):
        err = extract_compile_error(notes)
        missing, is_variant = analyze_compile_error(err)
        raw = f"""<details class="rawerr"><summary>Show the full compiler output</summary>
          <pre>{esc(err)}</pre></details>"""
        if cat == "wrongwork":
            names = ", ".join(f"<code>{esc(m)}(...)</code>" for m in missing[:3])
            if is_variant:
                what = (f"<p class='diag'>The official test for this question calls {names}, and the class in "
                        f"this submission has no such method. Its other methods also have different parameter "
                        f"and return types from the ones this question uses — so this is a <strong>different "
                        f"version of the class</strong>, not an incomplete one.</p>"
                        f"<p class='diag'><strong>What almost certainly happened:</strong> the exported JAR was "
                        f"another lab question's project, not this one's. Nothing here could be compiled, so no "
                        f"test could run and the score is 0.</p>"
                        f"<p class='hint'>What to check: open your submitted JAR and confirm the "
                        f"<code>.java</code> files inside are the ones you wrote for <em>this</em> question. If you "
                        f"believe the right project was submitted, take this to your TA.</p>")
            else:
                what = (f"<p class='diag'>The official test calls {names}, but no such method exists in the class "
                        f"submitted. The method this question asked you to write is <strong>not "
                        f"there</strong>, so nothing could be compiled and no test could run.</p>"
                        f"<p class='hint'>What to check: that the method name and its parameter list match the "
                        f"assignment exactly, and that the file you exported is the edited one rather than the "
                        f"untouched starter.</p>")
            return f'<div class="reason reason-bad">{what}{raw}</div>'
        first = (err or "").strip().splitlines()
        first_line = first[0] if first else ""
        return f"""<div class="reason reason-bad">
          <p class="diag">This submission did not compile against the official test, so no test could run. The first
          error <code>javac</code> reported was:</p>
          <pre>{esc(first_line)}</pre>
          <p class="hint">Fix the first error and recompile - the later ones are usually just consequences of it.
          <code>file.java:LINE:</code> names the file and line to open.</p>
          {raw}
        </div>"""
    if cat in ("structure", "nosource"):
        return f'<div class="reason reason-bad"><pre>{esc(notes)}</pre></div>'
    if cat == "timeout":
        return f"""<div class="reason reason-bad">
          <p>The test run for this submission <strong>did not finish within the time limit</strong> and was stopped, so it scored 0. This almost always means a loop that never ends &mdash; e.g. walking the list with an iterator without ever advancing it, or a remove/insert that loses the rest of the chain.</p>
          <p class="hint">Run the JUnit grader locally before submitting: a test that hangs instead of passing or failing is the signal to look for.</p>
        </div>"""
    if cat == "capped":
        m = re.search(r"SCORE CAPPED AT (\d+)%:\s*(.*)$", notes)
        pct = m.group(1) if m else row["score_cap"].strip("%")
        reason = m.group(2) if m else notes
        extra = ""
        if "more recent version of the Java Runtime" in row["failure_details"]:
            extra = '<p><strong>On top of that:</strong> the compiled classes were built with a newer Java version than the grading machine supports, so those classes could not even be loaded for testing.</p>'
        # The hint has to match the reason for THIS cap, not assume the no-source one.
        if pct == "0":
            hint = ('This cap is a marking-guide rule that sets the score to 0 outright, so it '
                    'replaces whatever the tests below did - any PASS listed there does not earn points here.')
        elif "precompiled .class" in reason or "no .java source" in reason:
            hint = ('Assignment policy: a JAR submitted without its <code>.java</code> source earns at most '
                    'half credit, even when the compiled code is correct. Tick "include source" when you '
                    'export the JAR, then open it with 7-Zip/WinRAR and confirm the .java files are in there.')
        elif "deeper archive" in reason or "nested" in reason:
            hint = ('Submit the project archive itself, not an archive wrapping another archive - the '
                    'grader had to dig through an extra layer to find anything gradable.')
        else:
            hint = 'The cap above is applied on top of the test results listed below.'
        return f"""<div class="reason reason-warn">
          <p><strong>Capped at {pct}%.</strong> {esc(reason)}</p>{extra}
          <p class="hint">{hint}</p>
        </div>"""
    if cat == "override":
        return f'<div class="reason reason-override"><pre>{esc(notes)}</pre></div>'
    if cat == "penalty":
        return f"""<div class="reason reason-warn">
          <p><strong>A rubric penalty was applied</strong> &mdash; a marking-guide rule that deducts a fixed number of points regardless of how the other tests went. The exact reason:</p>
          <pre>{esc(notes)}</pre>
          <p class="hint">The scored tests listed below are unaffected by this; the deduction is separate.</p>
        </div>"""
    return ""


def render_tests(row):
    if row["tests_total"] in ("", "0"):
        return ""
    passed = set(parse_pairs(row["passed_tests"]))
    fdetails = parse_failure_details(row["failure_details"])
    out = ['<div class="tests">']
    for cls, pts, methods in TEST_ORDER:
        out.append(f'<div class="tclass"><h4>{esc(cls)} <span class="pts">{pts} pts</span></h4>')
        for method, desc in methods:
            key = (cls, method)
            ok = key in passed
            detail = fdetails.get(key, "")
            cls_name = "t-pass" if ok else "t-fail"
            label = "PASS" if ok else "FAIL"
            det_html = ""
            if not ok:
                det_html = (f'<p class="tdetail">{esc(detail)}</p>' if detail
                             else '<p class="tdetail">No result at all for this test - usually because the whole test class failed to load.</p>')
            out.append(f"""<div class="trow {cls_name}">
              <span class="tbadge">{label}</span><span class="tname">{esc(method)}</span>
              <p class="twhat">{desc}</p>{det_html}
            </div>""")
        out.append("</div>")
    out.append("</div>")
    return "\n".join(out)


def render_graded_row(row):
    cat = category_of(row)
    sid = row["student_id"]
    score = row["score"]
    # A submission rejected before the rubric ran (compile/structure error) carries
    # max_score "0", which is a truthy STRING - so `or` never fired and the row read
    # "0/0", which tells a student nothing about what the work was out of.
    max_score = row["max_score"]
    if max_score in ("", "0", "0.0"):
        max_score = str(AUTOMATED_POINTS)
    summary_extra = ""
    if cat in ("compile", "structure", "nosource"):
        summary_extra = '<span class="note-flag">did not compile</span>'
    elif cat == "timeout":
        summary_extra = '<span class="note-flag">test run timed out</span>'
    elif cat == "capped":
        summary_extra = f'<span class="note-flag">capped {row["score_cap"]}</span>'
    elif cat == "override":
        summary_extra = '<span class="note-flag">TA reviewed</span>'
    elif cat == "penalty":
        summary_extra = '<span class="note-flag">rubric penalty</span>'
    if row["student_id"] in LATE_PENALTIES:
        summary_extra += '<span class="note-flag">late</span>'
    return f"""
    <details class="row" data-id="{esc(sid)}" data-cat="{cat}">
      <summary>
        <span class="sid">{esc(sid)}</span>
        <span class="chip chip-{cat}">{CATEGORY_LABEL[cat]}</span>
        {summary_extra}
        <span class="score">{score}<span class="of">/{max_score}</span></span>
      </summary>
      <div class="detail">
        {render_reason(row, cat)}
        {render_tests(row)}
      </div>
    </details>"""


def render_missing_row(sid, message):
    return f"""
    <details class="row" data-id="{esc(sid)}" data-cat="missing">
      <summary>
        <span class="sid">{esc(sid)}</span>
        <span class="chip chip-missing">{CATEGORY_LABEL['missing']}</span>
        <span class="score">&mdash;<span class="of">/{AUTOMATED_POINTS}</span></span>
      </summary>
      <div class="detail">
        <div class="reason reason-bad"><p>{esc(message)}</p></div>
      </div>
    </details>"""


CSS = """
:root {
  --paper: #f1f3f0; --paper-raised: #ffffff; --ink: #14171a; --ink-soft: #52585c;
  --line: #d7dcd6; --stain: #4a4fe0; --stain-soft: #eceeff;
  --pass: #1f8a5f; --pass-bg: #e8f5ee; --fail: #cf3652; --fail-bg: #fdeaee;
  --warn: #b17a12; --warn-bg: #fbf1de; --override-bg: #eef0f4; --override-line: #b9bfd6;
  --missing-bg: #f0f0f0; --code-bg: #1c1e26; --code-ink: #e6e6ea;
  --shadow: 0 1px 2px rgba(20,23,26,.04), 0 8px 24px -12px rgba(20,23,26,.12);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #14161a; --paper-raised: #1b1e23; --ink: #eceef0; --ink-soft: #a4abb3;
    --line: #2c3038; --stain: #8b90ff; --stain-soft: #23264a;
    --pass: #55c592; --pass-bg: #113023; --fail: #ff8098; --fail-bg: #3a1420;
    --warn: #e0b559; --warn-bg: #3a2d0f; --override-bg: #21242f; --override-line: #3c4160;
    --missing-bg: #202329; --code-bg: #0e0f13; --code-ink: #d7d9de;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px -12px rgba(0,0,0,.5);
  }
}
:root[data-theme="dark"] {
  --paper: #14161a; --paper-raised: #1b1e23; --ink: #eceef0; --ink-soft: #a4abb3;
  --line: #2c3038; --stain: #8b90ff; --stain-soft: #23264a;
  --pass: #55c592; --pass-bg: #113023; --fail: #ff8098; --fail-bg: #3a1420;
  --warn: #e0b559; --warn-bg: #3a2d0f; --override-bg: #21242f; --override-line: #3c4160;
  --missing-bg: #202329; --code-bg: #0e0f13; --code-ink: #d7d9de;
  --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px -12px rgba(0,0,0,.5);
}
* { box-sizing: border-box; }
html { color-scheme: light dark; }
body {
  margin: 0; background: var(--paper); color: var(--ink);
  font-family: "Public Sans", -apple-system, "Segoe UI", sans-serif;
  font-size: 15px; line-height: 1.55; -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 900px; margin: 0 auto; padding: 2.5rem 1.25rem 5rem; }
.mono { font-family: "JetBrains Mono", ui-monospace, Consolas, monospace; font-variant-numeric: tabular-nums; }
code { font-family: "JetBrains Mono", ui-monospace, monospace; background: var(--stain-soft); color: var(--stain); padding: .1em .4em; border-radius: 4px; font-size: .88em; }
header.top { margin-bottom: 1.75rem; }
.eyebrow { font-size: .78rem; letter-spacing: .09em; text-transform: uppercase; color: var(--stain); font-weight: 600; margin: 0 0 .5rem; }
h1 { font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: clamp(1.7rem, 3.5vw, 2.35rem); margin: 0 0 .6rem; text-wrap: balance; letter-spacing: -.01em; }
.scope-note { color: var(--ink-soft); font-size: .92rem; max-width: 62ch; }
.scope-note strong { color: var(--ink); }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: .7rem; margin: 1.75rem 0; }
.stat { background: var(--paper-raised); border: 1px solid var(--line); border-radius: 10px; padding: .85rem 1rem; box-shadow: var(--shadow); }
.stat .n { font-family: "Fraunces", serif; font-size: 1.5rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.stat .l { font-size: .74rem; color: var(--ink-soft); text-transform: uppercase; letter-spacing: .05em; margin-top: .15rem; }

.catbar { display: flex; flex-wrap: wrap; gap: .5rem; margin: 0 0 .75rem; }
.legend { margin: 0 0 1.75rem; padding: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fit, minmax(255px, 1fr)); gap: .3rem 1.4rem; font-size: .82rem; color: var(--ink-soft); line-height: 1.5; }
.legend b { color: var(--ink); font-weight: 600; }
.catbar .chip { cursor: pointer; user-select: none; }
.catbar .chip.active { outline: 2px solid var(--stain); outline-offset: 1px; }

.searchbar { display: flex; align-items: center; gap: .6rem; background: var(--paper-raised); border: 1px solid var(--line); border-radius: 10px; padding: .55rem .9rem; box-shadow: var(--shadow); margin-bottom: .6rem; }
.searchbar svg { flex: none; opacity: .5; }
#search { flex: 1; border: none; background: none; outline: none; color: var(--ink); font-size: .95rem; font-family: "JetBrains Mono", monospace; }
#search::placeholder { color: var(--ink-soft); }
.count { color: var(--ink-soft); font-size: .82rem; margin: 0 0 1rem .2rem; }

.list { display: flex; flex-direction: column; gap: .5rem; }
.row { background: var(--paper-raised); border: 1px solid var(--line); border-radius: 10px; box-shadow: var(--shadow); overflow: hidden; }
.row[hidden] { display: none; }
.row summary {
  list-style: none; cursor: pointer; display: flex; align-items: center; gap: .7rem;
  padding: .7rem .95rem; font-size: .92rem;
}
.row summary::-webkit-details-marker { display: none; }
.row summary::before {
  content: "\\25B8"; color: var(--ink-soft); font-size: .75rem; transition: transform .15s ease; width: .9rem; flex: none;
}
.row[open] summary::before { transform: rotate(90deg); }
.sid { font-family: "JetBrains Mono", monospace; font-weight: 500; letter-spacing: -.01em; }
.chip { font-size: .72rem; font-weight: 600; padding: .2em .55em; border-radius: 5px; letter-spacing: .01em; white-space: nowrap; }
.chip-pass { background: var(--pass-bg); color: var(--pass); }
.chip-partial { background: var(--warn-bg); color: var(--warn); }
.chip-capped, .chip-penalty { background: var(--warn-bg); color: var(--warn); }
.chip-compile, .chip-structure, .chip-nosource, .chip-timeout, .chip-wrongwork { background: var(--fail-bg); color: var(--fail); }
.rawerr { margin-top: .9rem; border-top: 1px solid var(--line); padding-top: .6rem; }
.rawerr > summary { cursor: pointer; font-size: .82rem; color: var(--ink-soft); list-style: none; }
.rawerr > summary::-webkit-details-marker { display: none; }
.rawerr > summary::before { content: "b8 "; display: inline-block; transition: transform .15s; }
.rawerr[open] > summary::before { transform: rotate(90deg); }
.rawerr > summary:hover { color: var(--ink); }
.diag { margin: 0 0 .5rem; }
.diag code { font-size: .9em; }
.chip-override { background: var(--override-bg); color: var(--ink); border: 1px solid var(--override-line); }
.chip-missing { background: var(--missing-bg); color: var(--ink-soft); }
.note-flag { font-size: .78rem; color: var(--ink-soft); }
.score { margin-left: auto; font-family: "Fraunces", serif; font-weight: 600; font-size: 1.05rem; font-variant-numeric: tabular-nums; flex: none; }
.score .of { font-family: "Public Sans", sans-serif; font-weight: 400; color: var(--ink-soft); font-size: .78rem; }

.detail { padding: 0 .95rem 1rem; border-top: 1px solid var(--line); margin-top: .1rem; }
.reason { padding-top: .85rem; font-size: .89rem; }
.reason p { margin: 0 0 .5rem; }
.reason .hint { color: var(--ink-soft); font-size: .84rem; }
.reason pre {
  background: var(--code-bg); color: var(--code-ink); padding: .8rem .9rem; border-radius: 8px;
  overflow-x: auto; font-size: .8rem; white-space: pre-wrap; word-break: break-word; font-family: "JetBrains Mono", monospace;
  margin: 0 0 .5rem;
}
.reason-override { border-radius: 8px; }
.reason-override pre { background: var(--override-bg); color: var(--ink); border: 1px solid var(--override-line); }

.tests { margin-top: .9rem; }
.tclass h4 { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; color: var(--ink-soft); font-weight: 600; margin: 1rem 0 .5rem; padding-top: .8rem; border-top: 1px solid var(--line); }
.tclass:first-child h4 { border-top: none; padding-top: 0; margin-top: 0; }
.pts { font-weight: 400; text-transform: none; letter-spacing: 0; opacity: .8; }
.trow { border-left: 3px solid var(--line); border-radius: 0 6px 6px 0; padding: .45rem .7rem; margin-bottom: .4rem; font-size: .85rem; }
.t-pass { border-left-color: var(--pass); }
.t-fail { border-left-color: var(--fail); background: var(--fail-bg); }
.tbadge { font-size: .68rem; font-weight: 700; letter-spacing: .04em; margin-right: .5rem; }
.t-pass .tbadge { color: var(--pass); }
.t-fail .tbadge { color: var(--fail); }
.tname { font-family: "JetBrains Mono", monospace; font-weight: 500; }
.twhat { margin: .3rem 0 0; color: var(--ink-soft); }
.tdetail { margin: .35rem 0 0; color: var(--fail); }

footer { text-align: center; color: var(--ink-soft); font-size: .8rem; margin-top: 2.5rem; }
a { color: var(--stain); }
:focus-visible { outline: 2px solid var(--stain); outline-offset: 2px; }
@media (max-width: 520px) {
  .row summary { flex-wrap: wrap; }
  .score { margin-left: 0; }
}
"""

JS = """
const rows = Array.from(document.querySelectorAll('.row'));
const search = document.getElementById('search');
const countEl = document.getElementById('count');
let activeCat = null;

function apply() {
  const q = search.value.trim().toLowerCase();
  let shown = 0;
  rows.forEach(r => {
    const idMatch = r.dataset.id.toLowerCase().includes(q);
    const catMatch = !activeCat || r.dataset.cat === activeCat;
    const visible = idMatch && catMatch;
    r.hidden = !visible;
    if (visible) shown++;
  });
  countEl.textContent = `Showing ${shown} of ${rows.length}`;
}
search.addEventListener('input', apply);
document.querySelectorAll('.catbar .chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const cat = chip.dataset.cat;
    activeCat = (activeCat === cat) ? null : cat;
    document.querySelectorAll('.catbar .chip').forEach(c => c.classList.toggle('active', c.dataset.cat === activeCat));
    apply();
  });
});
apply();
"""


def main():
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    scores = [float(r["score"]) for r in rows]
    compiled_yes = sum(1 for r in rows if r["compiled"] == "yes")
    full_pass = sum(1 for r in rows if category_of(r) == "pass")
    cats = {}
    for r in rows:
        c = category_of(r)
        cats[c] = cats.get(c, 0) + 1

    total_roster = len(rows) + len(NOT_SUBMITTED) + len(NO_VALID_Q2)

    stats_html = f"""
    <div class="stats">
      <div class="stat"><div class="n">{total_roster}</div><div class="l">Roster</div></div>
      <div class="stat"><div class="n">{len(rows)}</div><div class="l">Submitted &amp; graded</div></div>
      <div class="stat"><div class="n">{compiled_yes}/{len(rows)}</div><div class="l">Compiled</div></div>
      <div class="stat"><div class="n">{statistics.mean(scores):.1f}</div><div class="l">Mean score /{AUTOMATED_POINTS}</div></div>
      <div class="stat"><div class="n">{statistics.median(scores):.0f}</div><div class="l">Median /{AUTOMATED_POINTS}</div></div>
      <div class="stat"><div class="n">{full_pass}</div><div class="l">Full pass</div></div>
    </div>"""

    catbar_defs = [
        ("pass", "Full pass"), ("partial", "Partial"), ("capped", "Capped"),
        ("penalty", "Penalty"), ("timeout", "Timed out"),
        ("wrongwork", "Wrong submission"), ("compile", "Compile error"), ("structure", "Structure error"),
        ("nosource", "No source"), ("override", "TA review"), ("missing", "No submission"),
    ]
    catbar_html = '<div class="catbar">' + "".join(
        f'<span class="chip chip-{c} filterchip" data-cat="{c}">{label} ({cats.get(c, 0) if c != "missing" else len(NOT_SUBMITTED) + len(NO_VALID_Q2)})</span>'
        for c, label in catbar_defs if cats.get(c, 0) > 0 or c == "missing"
    ) + "</div>"

    CATEGORY_MEANING = {
        "pass": "Every scored test passed.",
        "partial": "Some tests passed, some failed - the failing ones are listed with the exact mismatch.",
        "capped": "A marking-guide rule limited the score, separately from how the tests went.",
        "penalty": "A fixed deduction from a marking-guide rule was applied.",
        "timeout": "The tests did not finish in the time limit and were stopped.",
        "wrongwork": "The class submitted is missing the method this question asked for - usually the wrong project was exported.",
        "compile": "The code did not compile against the official test, so no test could run.",
        "structure": "A class the assignment required was missing, so nothing could be compiled.",
        "nosource": "No usable .java source was found in the submission.",
        "override": "A TA reviewed this one by hand; the note explains what was decided.",
        "missing": "No gradable file was received for this assignment.",
    }
    shown = [c for c, _ in catbar_defs
             if cats.get(c, 0) > 0 or (c == "missing" and (NOT_SUBMITTED or NO_VALID_Q2))]
    legend_html = ('<ul class="legend">' + "".join(
        f'<li><b>{label}</b> &mdash; {CATEGORY_MEANING[c]}</li>'
        for c, label in catbar_defs if c in shown) + "</ul>")

    body_rows = [render_graded_row(r) for r in rows]
    for sid in NOT_SUBMITTED:
        body_rows.append(render_missing_row(sid, "No file was received for this assignment at all, per MyCourseVille's own submission log."))
    for sid in NO_VALID_Q2:
        body_rows.append(render_missing_row(sid, "A file was submitted, but it wasn't for this question - the attachment on record is unrelated to this assignment. Not gradable as submitted."))
    body_rows.sort(key=lambda h: re.search(r'data-id="(\d+)"', h).group(1))

    if TOTAL_POINTS:
        manual_points = TOTAL_POINTS - AUTOMATED_POINTS
        scope_note = (
            f'Every score below is the <strong>automated JUnit portion only ({AUTOMATED_POINTS} of {TOTAL_POINTS} total points)</strong>. '
            f'The remaining {manual_points} points, for {MANUAL_COMPONENT_DESC}, are graded separately by hand and are not reflected here yet. '
            f'Find your student ID below to see exactly which tests passed, which failed and why, and the reason behind any score adjustment.'
        )
    else:
        scope_note = (
            f'Find your student ID below to see exactly which tests passed, which failed and why, and the reason behind any score adjustment.'
        )

    html_out = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(ASSIGNMENT_TITLE)} Grading</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Public+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <p class="eyebrow">{ASSIGNMENT_EYEBROW}</p>
    <h1>{esc(ASSIGNMENT_TITLE)} &mdash; Grading Report</h1>
    <p class="scope-note">{scope_note}</p>
  </header>

  {stats_html}
  {catbar_html}
    {legend_html}

  <div class="searchbar">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input id="search" type="text" placeholder="Search your student ID&hellip;" autocomplete="off" spellcheck="false">
  </div>
  <p class="count" id="count"></p>

  <div class="list">
    {''.join(body_rows)}
  </div>

  <footer>Auto-generated from the course's JUnit grading run &middot; questions about a specific result are welcome in office hours.</footer>
</div>
<script>{JS}</script>
</body>
</html>
"""
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html_out, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")
    print(f"Rows: {len(body_rows)}  Categories: {cats}")
    print("Now publish OUT_PATH with the Artifact tool.")


if __name__ == "__main__":
    main()
