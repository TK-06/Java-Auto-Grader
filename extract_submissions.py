#!/usr/bin/env python3
"""Extract one submission per student from MyCourseVille bulk-export zip(s).

This is the step between downloading MCV's export and running grade.py, and the
one that was previously done by hand every week. It reads the export zip(s)
directly and writes exactly one file per student into submissions/, named
<studentID>.<ext> - which is what grade.py then uses as the CSV's student_id.

Never hand an MCV export straight to grade.py. Each zip has one top-level folder
per student ID holding every file that student attached to that assignment SLOT,
which is not the same as "every file is a valid answer to this question":

- A folder often holds BOTH questions' files (MCV does not filter by question),
  so a file naming some other question is dropped - see is_wrong_question in
  check_lateness.py.
- A student can resubmit. The authoritative "which is latest" signal is the
  timestamp encoded in the filename, NOT the zip's own log.txt (an unordered
  processing log). Decoding is shared with check_lateness.decode_timestamp, so
  this script and the late-penalty tool can never disagree about which file is
  a given student's real final submission.
- The FOLDER NAME is authoritative for whose work a file is. A filename
  carrying a different student's ID, or a typo'd one, is still that folder's
  submission: it is kept and flagged, never reassigned.
- Occasionally a folder's only file is not an answer at all (a stray PDF from
  another course). Non-archives are dropped.
- log.txt's "No file for <id>" lines are the only record of students who
  submitted nothing at all - they have no folder, so they are otherwise
  invisible once you are looking at extracted files. They are reported here
  because tests/report_config.json's not_submitted needs exactly that list.

Windows' 260-character path limit is why each chosen entry is streamed straight
to its short final <studentID>.<ext> name rather than extracted with its
original (very long) MCV path preserved.

This deliberately discards the timestamp the original filename carried. That is
not a mistake to fix here: check_lateness.py recovers real submission times from
the original zip(s), never from submissions/ - so keep the zip(s) until it has
run.

Usage:
    python extract_submissions.py ZIP [ZIP ...] --question 1
    python extract_submissions.py ZIP [ZIP ...] --question 1 --dry-run
    python extract_submissions.py ZIP [ZIP ...] --question 1 --out submissions
"""
import argparse
import json
import shutil
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import check_lateness as cl

# An archive this small almost never holds a real exported project - usually
# source-only with no .class, or a near-empty export. Never dropped and not an
# error; just surfaced so a TA can eyeball it before grading.
SMALL_ARCHIVE_BYTES = 30_000


def collect(zip_paths):
    """Group every entry by its top-level folder (the student ID).

    Returns (by_student, no_file, notes): by_student maps student_id ->
    [(zip_path, entry, filename, timestamp), ...]; no_file is the IDs read out
    of each zip's own log.txt "No file for <id>" lines; notes records anything
    ignored, so nothing is ever silently discarded."""
    by_student = defaultdict(list)
    no_file = []
    notes = []
    for zpath in zip_paths:
        try:
            zf = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile as exc:
            sys.exit(f"ERROR: {zpath} is not a valid zip file: {exc}")
        with zf:
            for entry in zf.namelist():
                if entry.endswith("/"):
                    continue
                parts = entry.split("/", 1)
                if len(parts) != 2:
                    if Path(entry).name == "log.txt":
                        text = zf.read(entry).decode("utf-8", "ignore")
                        for line in text.splitlines():
                            if "No file for" in line:
                                no_file.append(
                                    line.split("No file for", 1)[1].strip().strip(":")
                                )
                    continue
                student_id, filename = parts
                if not student_id.isdigit():
                    notes.append(f"{zpath.name}: ignored non-student entry {entry!r}")
                    continue
                by_student[student_id].append(
                    (zpath, entry, filename, cl.decode_timestamp(filename))
                )
    return by_student, sorted(set(no_file)), notes


def choose(by_student, question):
    """Pick each student's latest valid submission for `question`.

    Returns (chosen, flags, unusable). flags is everything a TA should look at
    rather than trust blindly - resubmissions, a filename bearing someone
    else's ID, a filename naming no question at all - and is deliberately
    reported, never acted on silently."""
    chosen, flags, unusable = {}, [], []
    for student_id, items in sorted(by_student.items()):
        keep = []
        for zpath, entry, filename, ts in items:
            if cl.is_not_an_archive(filename):
                flags.append(f"{student_id}: dropped non-archive file {filename!r}")
            elif cl.is_wrong_question(filename, question):
                flags.append(f"{student_id}: dropped other-question file {filename!r}")
            elif ts is None:
                flags.append(
                    f"{student_id}: dropped {filename!r} - no decodable timestamp"
                )
            else:
                keep.append((zpath, entry, filename, ts))
        if not keep:
            unusable.append(student_id)
            flags.append(
                f"{student_id}: NO USABLE FILE - {len(items)} file(s) present, "
                f"none gradable"
            )
            continue
        keep.sort(key=lambda t: t[3])
        winner = keep[-1]
        if len(keep) > 1:
            flags.append(
                f"{student_id}: {len(keep)} submissions, kept latest {winner[2]!r} "
                f"({winner[3]:%Y-%m-%d %H:%M} ICT)"
            )
        if student_id not in winner[2]:
            flags.append(
                f"{student_id}: kept file's name does not contain this student's ID "
                f"({winner[2]!r}) - the folder is authoritative, kept anyway"
            )
        if "Q" not in winner[2].upper():
            flags.append(f"{student_id}: kept file names no question ({winner[2]!r})")
        chosen[student_id] = winner
    return chosen, flags, unusable


def clear_output_dir(out_dir, dry_run):
    """Empty out_dir so last week's submissions can't be graded by accident.
    .gitkeep is preserved - it is the tracked placeholder that keeps this
    gitignored directory present in a fresh clone."""
    removed = []
    for path in sorted(out_dir.iterdir()):
        if path.name == ".gitkeep":
            continue
        removed.append(path.name)
        if not dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    return removed


def write_submissions(chosen, out_dir, dry_run):
    written, small = [], []
    for student_id, (zpath, entry, filename, _ts) in sorted(chosen.items()):
        dest = out_dir / f"{student_id}{Path(filename).suffix.lower()}"
        if dry_run:
            with zipfile.ZipFile(zpath) as zf:
                size = zf.getinfo(entry).file_size
        else:
            # Streamed straight to the short final name - the original long MCV
            # path is never recreated on disk (Windows 260-char limit).
            with zipfile.ZipFile(zpath) as zf, open(dest, "wb") as fh:
                shutil.copyfileobj(zf.open(entry), fh)
            size = dest.stat().st_size
        written.append(dest.name)
        if size < SMALL_ARCHIVE_BYTES:
            small.append((student_id, size))
    return written, small


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract one submission per student (latest wins) from "
                    "MyCourseVille bulk-export zip(s) into submissions/."
    )
    parser.add_argument("zips", nargs="+", help="MyCourseVille bulk-export zip file(s)")
    parser.add_argument(
        "--question", type=int, required=True,
        help="question number being graded. Required on purpose: a wrong guess "
             "here silently extracts the other question's files for the whole class.",
    )
    parser.add_argument(
        "--out", default="submissions",
        help="output directory, emptied first except .gitkeep (default: submissions)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report exactly what would happen and write nothing",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    zip_paths = [Path(z) for z in args.zips]
    missing = [str(z) for z in zip_paths if not z.is_file()]
    if missing:
        sys.exit("ERROR: zip file(s) not found:\n  " + "\n  ".join(missing))

    out_dir = Path(args.out)
    if not out_dir.is_dir():
        sys.exit(f"ERROR: output directory not found: {out_dir}")

    by_student, no_file, notes = collect(zip_paths)
    if not by_student:
        sys.exit(
            "ERROR: no <studentID>/ folders found in the given zip(s) - is this "
            "really an MCV bulk-attachment export?"
        )

    chosen, flags, unusable = choose(by_student, args.question)
    removed = clear_output_dir(out_dir, args.dry_run)
    written, small = write_submissions(chosen, out_dir, args.dry_run)

    verb = "Would write" if args.dry_run else "Wrote"
    print(f"Read {len(zip_paths)} zip file(s), grading Q{args.question}.")
    print(f"  student folders in export : {len(by_student)}")
    print(f"  {verb:<11} to {out_dir}/  : {len(written)}")
    print(f"  submitted nothing at all  : {len(no_file)}")
    print(f"  submitted, nothing usable : {len(unusable)}")
    if removed:
        print(f"  cleared beforehand        : {len(removed)} existing file(s)")

    if notes:
        print(f"\nIgnored entries ({len(notes)}):")
        for note in notes:
            print(f"  {note}")

    if flags:
        print(f"\nNeeds a look ({len(flags)}):")
        for flag in flags:
            print(f"  {flag}")

    if small:
        print(
            f"\nUnusually small archives (under {SMALL_ARCHIVE_BYTES:,} bytes) - "
            f"confirm these really contain a project ({len(small)}):"
        )
        for student_id, size in small:
            print(f"  {student_id}: {size:,} bytes")

    print("\nFor tests/report_config.json:")
    print(f'  "not_submitted": {json.dumps(no_file)}')
    if unusable:
        print(f'  "no_valid_q{args.question}": {json.dumps(sorted(unusable))}')

    if args.dry_run:
        print(f"\n(dry run - {out_dir}/ was not modified)")
    else:
        print("\nKeep the export zip(s) until check_lateness.py has run.")
        print("Next: python grade.py")


if __name__ == "__main__":
    main()
