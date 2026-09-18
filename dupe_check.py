"""Group students whose student-written code is identical.

Comparison ignores whitespace, comments and string literals, so identical here
means identical logic AND identical variable names AND identical structure --
not merely a similar approach.

Two ways to point it at the student's own work, because most of a submission is
code the starter already gave them and comparing whole files would match
everyone:

  --whole-file <Class>   the class exists only because the student wrote it
                         (nothing was given), so compare the entire file
  --method <name>        for classes that were mostly given, compare only this
                         method plus every helper it calls

A match is evidence for a human to weigh, never a score consequence: short
methods can legitimately coincide, so read the flagged pairs before concluding
anything. Agreement across SEVERAL independently written methods is the signal
worth attending to -- that is why the summary reports per-class groups rather
than one verdict.

Read-only: reads submissions/*.jar, writes nothing.

    python dupe_check.py --whole-file StackUtility \
        --method removeRange --classes StackArray,StackLinkedList
"""
import re
import sys
import json
import zipfile
import pathlib
import hashlib
import argparse
import itertools
import collections

DECL_RE = re.compile(
    r"(?:public|private|protected|static|final|synchronized)\s[\w\[\]<>,. ]*?\s(\w+)\s*\([^)]*\)\s*(?:throws[\w\s,.]+?)?\{"
)


def strip_noise(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    return text


def normalise(text):
    return re.sub(r"\s+", " ", strip_noise(text)).strip()


def methods(text):
    out = {}
    t = strip_noise(text)
    for m in DECL_RE.finditer(t):
        name, start = m.group(1), m.end() - 1
        depth, j = 0, start
        while j < len(t):
            if t[j] == "{":
                depth += 1
            elif t[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out[name] = out.get(name, "") + t[start:j + 1]
    return out


def student_written(text, entry):
    """The entry method plus every helper it reaches, order-independent."""
    ms = methods(text)
    if entry not in ms:
        return None
    seen, queue, parts = {entry}, [entry], []
    while queue:
        name = queue.pop()
        body = ms.get(name, "")
        parts.append(body)
        for call in re.findall(r"(\w+)\s*\(", body):
            if call in ms and call not in seen:
                seen.add(call)
                queue.append(call)
    return re.sub(r"\s+", " ", "".join(sorted(parts))).strip()


def source_of(zf, cls):
    names = [n for n in zf.namelist()
             if n.endswith("/" + cls + ".java") or n == cls + ".java"]
    return zf.read(sorted(names)[0]).decode("utf-8", "replace") if names else None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submissions", default="submissions",
                    help="directory of student .jar files (default: submissions)")
    ap.add_argument("--whole-file", default="",
                    help="comma-separated classes to compare in full (student wrote all of them)")
    ap.add_argument("--classes", default="",
                    help="comma-separated classes to compare by method only (mostly given code)")
    ap.add_argument("--method", default="",
                    help="the student-written method in --classes, e.g. removeRange")
    args = ap.parse_args()

    whole = [c.strip() for c in args.whole_file.split(",") if c.strip()]
    partial = [c.strip() for c in args.classes.split(",") if c.strip()]
    if partial and not args.method:
        ap.error("--classes needs --method (which method in them the student wrote)")
    if not whole and not partial:
        ap.error("give --whole-file and/or --classes")

    buckets = {c: collections.defaultdict(list) for c in whole + partial}
    for jar in sorted(pathlib.Path(args.submissions).glob("*.jar")):
        try:
            zf = zipfile.ZipFile(jar)
        except Exception:
            continue
        for cls in buckets:
            text = source_of(zf, cls)
            if text is None:
                continue
            body = normalise(text) if cls in whole else student_written(text, args.method)
            if not body:
                continue
            buckets[cls][hashlib.sha256(body.encode()).hexdigest()[:16]].append(jar.stem)

    report, pair_hits = {}, collections.Counter()
    for cls, groups in buckets.items():
        dupes = {h: ids for h, ids in groups.items() if len(ids) > 1}
        for ids in dupes.values():
            for a, b in itertools.combinations(sorted(ids), 2):
                pair_hits[(a, b)] += 1
        report[cls] = {
            "students_compared": sum(len(v) for v in groups.values()),
            "distinct_solutions": len(groups),
            "groups_of_identical": sorted(dupes.values(), key=len, reverse=True),
        }

    # A pair matching on EVERY compared class is far stronger evidence than one
    # matching on a single short method, so it is called out separately.
    n = len(buckets)
    report["_pairs_identical_in_all_%d_classes" % n] = [
        list(p) for p, c in sorted(pair_hits.items()) if c == n
    ]
    print(json.dumps(report, indent=2))
    print("compared %d class(es) across the cohort" % n, file=sys.stderr)


if __name__ == "__main__":
    main()
