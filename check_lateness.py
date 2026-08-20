#!/usr/bin/env python3
"""Compute each student's real submission timestamp, and (with --deadline)
the late-penalty multiplier it implies, from MyCourseVille bulk-export zips.

grade.py's late policy -- 10% docked per day late, see README.md's "Grading
policy (SLA)" section -- has no deadline awareness of its own; working out
each submission's days-late has so far been a manual step done by hand.
This script does that step, standalone, without touching grade.py's own
input/output files at all.

It reads one or more MyCourseVille "bulk download" zip exports directly --
the kind with one top-level folder per student ID (e.g. "6738201021/"),
containing that student's submitted file(s) named like
<name>-<attachmentID>-<timestamp>.<ext>. This is NOT the same as
submissions/: those files were renamed to <studentID>.<ext> during
extraction and no longer carry the original MCV filename or timestamp, so
real lateness can only be recovered from the original export zips.

Timestamp encoding (two formats seen in real exports this course has used):
  - Most files end in -<14-digit number>.<ext>. That number is Unix epoch
    seconds * 10000, UTC.
  - A smaller subset end in _<8 hex chars>_<10-digit number>.<ext>. That
    10-digit number is already plain Unix epoch seconds, UTC.
Both are converted to Asia/Bangkok local time (MCV's own displayed
submission time) for output.

A student folder can contain more than one file -- resubmissions, or
occasionally a different question's file bundled in alongside the target
one. This script keeps only files that plausibly belong to --question
(default 2): a file naming some OTHER question number and never also
naming the target is dropped (e.g. a stray "..._Week2_Q1-....jar" sitting
next to "..._Week2_Q2-....jar" is dropped when --question is 2; a filename
that names no question at all is never dropped on this basis). Among what
survives, the LATEST decoded timestamp is that student's real, final
submission.

This tool never touches submissions/ or results/grades.csv -- grades.csv
stays exactly as grade.py wrote it, so it always keeps the pre-late-penalty
technical record intact for audit purposes (what actually compiled, what
actually passed, what grade.py's own caps were).

When --deadline is given, it DOES automatically rewrite results/mcvScore.csv
(the file that actually gets uploaded) -- for each student found late, their
mcvScore.csv score is capped at whichever is stricter: their existing
score_cap from grades.csv (if any) or the late penalty. The two are never
multiplied together, only compared -- a submission already capped at 50%
for some other reason (e.g. compiled-class-only) that's also 1 day late
(which alone would only cap at 90%) stays at 50%, not 45%, since the late
penalty only binds when it's the stricter constraint. A submission with no
other cap that's 1 day late gets the full 90% (10% off), same as always. A
row with a "TA OVERRIDE:" note in grades.csv is never auto-adjusted, even if
that student is also late -- a human already made a deliberate call there.
mcvScore.csv is fully regenerated from grades.csv each run (not patched
incrementally), so re-running with the same deadline is always safe and
gives identical output -- there's no double-penalty risk to guard against.
The previous copy is backed up to mcvScore.csv.bak first.

Usage:
    python check_lateness.py ZIP [ZIP ...]
    python check_lateness.py ZIP [ZIP ...] --deadline 2026-08-19T23:59:00
    python check_lateness.py ZIP [ZIP ...] --question 1
    python check_lateness.py ZIP [ZIP ...] --deadline ... --grades results/grades.csv --scores results/mcvScore.csv
"""
import argparse
import csv
import math
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import grade

BANGKOK = ZoneInfo("Asia/Bangkok")

# Trailing -<14-digit>.<ext>: epoch seconds * 10000, UTC.
TS_SCALED_RE = re.compile(r"-(\d{14})\.[A-Za-z0-9]+$")
# Trailing _<8 hex chars>_<10-digit>.<ext>: plain epoch seconds, UTC.
TS_HEX_RE = re.compile(r"_([0-9a-fA-F]{8})_(\d{10})\.[A-Za-z0-9]+$")

# A question marker anywhere in a filename: "Q2", "Question2", "Question 2",
# "Q_2", "Q1_1517413_..." (a real bundled-file pattern seen this term), etc.
# The negative lookahead (not \b) keeps "Q1" from matching inside "Q10" while
# still firing when the digit is immediately followed by "_" -- \b does NOT
# fire there, since digit and "_" are both "word" characters to regex.
QUESTION_RE = re.compile(r"q(?:uestion)?[\s_]?(\d+)(?!\d)", re.IGNORECASE)


@dataclass
class Submission:
    student_id: str
    zip_name: str
    entry: str          # full path inside the zip, e.g. "6738201021/foo-123.jar"
    filename: str        # just the basename, e.g. "foo-123.jar"
    timestamp: "datetime | None"  # decoded Asia/Bangkok local time, or None if undecodable


def decode_timestamp(filename: str) -> "datetime | None":
    """Decode a submission's real timestamp from its MCV filename, per the
    two encodings documented in the module docstring. Returns None (never a
    guess) if neither known pattern matches."""
    m = TS_HEX_RE.search(filename)
    if m:
        epoch_seconds = int(m.group(2))
        utc = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        return utc.astimezone(BANGKOK)
    m = TS_SCALED_RE.search(filename)
    if m:
        raw = int(m.group(1))
        epoch_seconds = raw / 10000
        utc = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        return utc.astimezone(BANGKOK)
    return None


def question_numbers(filename: str) -> set:
    return {int(n) for n in QUESTION_RE.findall(filename)}


VALID_EXTENSIONS = {".jar", ".zip"}


def is_not_an_archive(filename: str) -> bool:
    """True for anything that isn't a .jar/.zip submission at all (e.g. a
    student's folder occasionally contains a stray PDF or other unrelated
    file with nothing to do with the assignment). Checked independently of
    is_wrong_question - a non-archive file's name never gets a fair chance
    to "name the target question" in the first place, so it must be
    excluded on its own, not folded into that check."""
    return Path(filename).suffix.lower() not in VALID_EXTENSIONS


def is_wrong_question(filename: str, target: int) -> bool:
    """True if `filename` should be excluded because it names some question
    other than `target` and never also names `target` itself. A filename
    mentioning no question number at all is never excluded on this basis --
    this only screens out another question's file bundled into the same
    student folder (e.g. a Week 2 Q1 jar sitting alongside the Q2 one)."""
    numbers = question_numbers(filename)
    other = numbers - {target}
    return bool(other) and target not in numbers


def collect_submissions(zip_paths: "list[Path]") -> "tuple[dict, list]":
    """Read every zip and group entries by student ID (each zip's top-level
    folder name). Returns (by_student, notes); notes lists anything skipped
    or unparseable so it's visible, never silently dropped."""
    by_student = {}
    notes = []

    for zpath in zip_paths:
        try:
            zf = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile as e:
            sys.exit(f"ERROR: {zpath} is not a valid zip file: {e}")
        with zf:
            for entry in zf.namelist():
                if entry.endswith("/"):
                    continue  # explicit directory entry, no file here
                parts = entry.split("/", 1)
                if len(parts) != 2:
                    # Root-level file, not inside any student folder (e.g.
                    # MCV's own "log.txt" export manifest) -- not a submission.
                    continue
                student_id, filename = parts
                if not student_id.isdigit():
                    notes.append(f"{zpath.name}: skipped non-student top-level entry {entry!r}")
                    continue
                ts = decode_timestamp(filename)
                if ts is None:
                    notes.append(
                        f"{zpath.name}: could not decode a timestamp from {entry!r} "
                        f"-- neither known filename pattern matched it"
                    )
                sub = Submission(
                    student_id=student_id,
                    zip_name=zpath.name,
                    entry=entry,
                    filename=filename,
                    timestamp=ts,
                )
                by_student.setdefault(student_id, []).append(sub)

    return by_student, notes


def pick_latest(subs: "list[Submission]", question: int) -> "tuple[Submission | None, list[Submission]]":
    """From one student's raw submission list, drop wrong-question files and
    undecodable ones, then return (latest, excluded): latest is the
    surviving submission with the latest timestamp (None if nothing
    survives), excluded is everything filtered out (for transparency)."""
    excluded = []
    candidates = []
    for sub in subs:
        if is_not_an_archive(sub.filename):
            excluded.append(sub)
            continue
        if is_wrong_question(sub.filename, question):
            excluded.append(sub)
            continue
        if sub.timestamp is None:
            excluded.append(sub)
            continue
        candidates.append(sub)
    if not candidates:
        return None, excluded
    latest = max(candidates, key=lambda s: s.timestamp)
    return latest, excluded


def days_late(submitted: datetime, deadline: datetime) -> int:
    """Days late, rounding any partial day UP to a full day (30 minutes
    past the deadline counts as 1 day late, not 0). 0 if on time or early."""
    seconds_late = (submitted - deadline).total_seconds()
    if seconds_late <= 0:
        return 0
    return math.ceil(seconds_late / 86400)


def late_multiplier(days: int) -> float:
    """10% docked per day late (README.md): 0 days -> 1.0, 1 -> 0.9, ...
    10+ days -> 0.0. Built from integer arithmetic so the steps land on
    exact values (1.0, 0.9, 0.8, ...) instead of accumulating float error."""
    capped_days = min(days, 10)
    return (10 - capped_days) / 10


def parse_cap_fraction(score_cap: str) -> float:
    """Parse grades.csv's score_cap column ("", "50%", "0%", ...) into a
    0.0-1.0 fraction. Blank means no cap is in effect (1.0)."""
    text = score_cap.strip()
    if not text:
        return 1.0
    return float(text.rstrip("%")) / 100.0


def read_grades_csv(path: Path) -> "list[dict]":
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def apply_late_caps(grades_rows: "list[dict]", late_by_student: dict) -> "tuple[list[dict], list[str]]":
    """Computes what results/mcvScore.csv should contain given the current
    grades.csv rows and this run's late/multiplier results (student_id ->
    row from the days_late table above, only for students with a valid
    submission). Returns (mcv_rows, report) -- mcv_rows is grades_rows with
    each late student's "score" replaced by the late-capped value (everyone
    else's copied through unchanged); report is human-readable lines
    describing every adjustment (or skip) made, for visibility.

    grades.csv itself is never mutated or written by this function -- caller
    only ever passes the result to write_scores_csv, never back to
    write_csv. A late student's new cap is min(existing cap, late
    multiplier), never existing * late -- see module docstring for why."""
    mcv_rows = []
    report = []
    for row in grades_rows:
        bare_id = grade.bare_student_id(row["student_id"])
        new_row = dict(row)
        late = late_by_student.get(bare_id)
        if late is not None and late["days_late"] > 0:
            if "TA OVERRIDE:" in row["notes"]:
                report.append(
                    f"  {row['student_id']}: {late['days_late']}d late, but grades.csv has a "
                    f"TA OVERRIDE note -- left mcvScore.csv score as-is ({row['score']}), "
                    f"not auto-adjusted"
                )
            else:
                uncapped = float(row["uncapped_score"])
                max_score = float(row["max_score"])
                existing_cap = parse_cap_fraction(row["score_cap"])
                new_cap = min(existing_cap, late["multiplier"])
                # round() to sidestep float noise (e.g. 11.0 * 0.7 ==
                # 7.699999999999999) -- this is the number written straight
                # into mcvScore.csv, which gets uploaded as-is.
                new_score = round(min(uncapped, new_cap * max_score), 2)
                if new_score != float(row["score"]):
                    new_row["score"] = new_score
                    report.append(
                        f"  {row['student_id']}: {late['days_late']}d late (late cap "
                        f"{late['multiplier']:.0%}) -- existing cap {existing_cap:.0%}, "
                        f"effective cap {new_cap:.0%}: mcvScore.csv score "
                        f"{row['score']} -> {new_score:g}"
                    )
        mcv_rows.append(new_row)
    return mcv_rows, report


def parse_deadline(text: str) -> datetime:
    """Parse an ISO datetime string as Asia/Bangkok local time. If the
    string already carries its own UTC offset, that offset is honored and
    the result is just converted to Bangkok for display/comparison."""
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BANGKOK)
    return dt.astimezone(BANGKOK)


def fmt_ts(dt: datetime) -> str:
    # Thailand does not observe DST, so the offset is always +07:00 --
    # "ICT" (Indochina Time) is unambiguous here.
    return dt.strftime("%Y-%m-%d %H:%M:%S") + " ICT"


def render_table(headers: "list[str]", rows: "list[list[str]]") -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decode each student's real submission timestamp from MyCourseVille "
                    "bulk-export zip(s), and (with --deadline) the late-penalty multiplier "
                    "it implies under the 10%-per-day-late policy in README.md."
    )
    parser.add_argument("zips", nargs="+", help="MyCourseVille bulk-export zip file(s) to read")
    parser.add_argument(
        "--question", type=int, default=2,
        help="target question number to keep when a student's folder has another "
             "question's file bundled in alongside it (default: 2)",
    )
    parser.add_argument(
        "--deadline", default=None,
        help="ISO datetime (e.g. 2026-08-19T23:59:00), interpreted as Asia/Bangkok local "
             "time unless it carries its own UTC offset. When given, adds days_late and "
             "late_multiplier columns to the output, AND automatically rewrites "
             "--scores with each late student's score capped (never grades.csv -- see "
             "module docstring).",
    )
    parser.add_argument(
        "--grades", default=str(Path("results") / "grades.csv"),
        help="grades.csv to read scores/caps from when --deadline is given; never modified "
             "(default: results/grades.csv)",
    )
    parser.add_argument(
        "--scores", default=str(Path("results") / "mcvScore.csv"),
        help="mcvScore.csv to rewrite with late-adjusted scores when --deadline is given "
             "(default: results/mcvScore.csv)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    zip_paths = [Path(z) for z in args.zips]
    missing = [str(z) for z in zip_paths if not z.is_file()]
    if missing:
        sys.exit("ERROR: zip file(s) not found:\n  " + "\n  ".join(missing))

    deadline = None
    if args.deadline is not None:
        try:
            deadline = parse_deadline(args.deadline)
        except ValueError as e:
            sys.exit(f"ERROR: could not parse --deadline {args.deadline!r} as an ISO datetime: {e}")

    by_student, notes = collect_submissions(zip_paths)

    rows = []
    no_valid = []
    multi_candidate = []  # students with >1 file total, for the diagnostics section
    for student_id, subs in by_student.items():
        latest, excluded = pick_latest(subs, args.question)
        if len(subs) > 1:
            multi_candidate.append((student_id, subs, latest))
        if latest is None:
            no_valid.append((student_id, subs, excluded))
            continue
        row = {
            "student_id": student_id,
            "timestamp": latest.timestamp,
            "filename": latest.filename,
            "files_found": len(subs),
        }
        if deadline is not None:
            d = days_late(latest.timestamp, deadline)
            row["days_late"] = d
            row["multiplier"] = late_multiplier(d)
        rows.append(row)

    rows.sort(key=lambda r: int(r["student_id"]))

    headers = ["student_id", "submitted (Asia/Bangkok)", "files_found", "source_file"]
    if deadline is not None:
        headers += ["days_late", "multiplier"]

    table_rows = []
    for r in rows:
        cells = [r["student_id"], fmt_ts(r["timestamp"]), str(r["files_found"]), r["filename"]]
        if deadline is not None:
            cells += [str(r["days_late"]), f'{r["multiplier"]:.1f}']
        table_rows.append(cells)

    print(f"Read {len(zip_paths)} zip file(s), {len(by_student)} student folder(s) total.")
    if deadline is not None:
        print(f"Deadline: {fmt_ts(deadline)}")
    print(f"Target question: Q{args.question}")
    print()
    print(render_table(headers, table_rows))
    print()
    print(f"{len(rows)} student(s) with a valid Q{args.question} submission.")

    if no_valid:
        print()
        print(f"{len(no_valid)} student(s) with NO valid Q{args.question} submission found:")
        for student_id, subs, excluded in sorted(no_valid, key=lambda t: int(t[0])):
            reasons = ", ".join(f"{s.filename} ({s.zip_name})" for s in excluded)
            print(f"  {student_id}: {len(subs)} file(s) seen, none usable -- {reasons}")

    if multi_candidate:
        print()
        print(f"{len(multi_candidate)} student(s) had more than one candidate file "
              f"(latest one shown above was picked):")
        for student_id, subs, latest in sorted(multi_candidate, key=lambda t: int(t[0])):
            print(f"  {student_id}:")
            # datetime.min fallback (not None) so two undecodable files never
            # need "None < None", which Python can't compare.
            for s in sorted(subs, key=lambda s: s.timestamp or datetime.min.replace(tzinfo=BANGKOK)):
                marker = "-> " if s is latest else "   "
                ts_str = fmt_ts(s.timestamp) if s.timestamp is not None else "UNDECODABLE"
                if is_not_an_archive(s.filename):
                    excl = "  [excluded: not a .jar/.zip]"
                elif s.timestamp is not None and is_wrong_question(s.filename, args.question):
                    excl = "  [excluded: wrong question]"
                else:
                    excl = ""
                print(f"    {marker}{ts_str}  {s.filename}  ({s.zip_name}){excl}")

    if notes:
        print()
        print("Notes:")
        for n in notes:
            print(f"  {n}")

    if deadline is not None and rows:
        late_by_student = {r["student_id"]: r for r in rows}
        grades_path = Path(args.grades)
        scores_path = Path(args.scores)
        if not grades_path.is_file():
            sys.exit(
                f"ERROR: --grades file not found: {grades_path} "
                f"(run grade.py first, or pass the correct path)"
            )
        write_err = grade.check_output_writable(scores_path)
        if write_err:
            sys.exit(f"ERROR: {write_err}")

        grades_rows = read_grades_csv(grades_path)
        mcv_rows, report = apply_late_caps(grades_rows, late_by_student)

        if scores_path.is_file():
            backup_path = scores_path.parent / (scores_path.name + ".bak")
            shutil.copyfile(scores_path, backup_path)

        grade.write_scores_csv(mcv_rows, scores_path)

        print()
        if report:
            print(f"Applied late penalty to {scores_path} (previous copy backed up to "
                  f"{scores_path.name}.bak):")
            for line in report:
                print(line)
        else:
            print(f"Rewrote {scores_path} from {grades_path} -- no late-student score "
                  f"changes were needed.")


if __name__ == "__main__":
    main()
