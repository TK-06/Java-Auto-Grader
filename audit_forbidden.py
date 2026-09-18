"""Audit a "do not call these methods" rule over the real call graph, and report
where that disagrees with the official test's own text scan.

Some marking guides forbid a method from calling certain others -- "write
removeRange by manipulating the array directly; push/pop/top are forbidden".
The official test usually enforces that by scanning the SOURCE TEXT from the
method's declaration to end of file. That has a structural blind spot: a
forbidden call inside a private helper declared ABOVE the entry method is
invisible to it, and a forbidden name appearing in a comment or a string is a
false positive.

This re-asks the same question over the file's actual call graph -- the entry
method plus every helper it reaches, wherever those are declared -- and prints
only the students where the two answers differ. Those are the rows a TA should
look at by hand; the automated score stays whatever the official test said,
because grading students more harshly than the official check does is a policy
call, not a tooling one.

Read-only: reads submissions/*.jar, writes nothing.

    python audit_forbidden.py --entry removeRange \
        --classes StackArray,StackLinkedList \
        --forbidden pop,top,push,makeEmpty,findKth,insert,find,findPrevious,remove,removeAt,printList,getTheArray
"""
import re
import sys
import json
import zipfile
import pathlib
import argparse

DECL_RE = re.compile(
    r"(?:public|private|protected|static|final|synchronized)\s[\w\[\]<>,. ]*?\s(\w+)\s*\([^)]*\)\s*(?:throws[\w\s,.]+?)?\{"
)


def strip_noise(text):
    """Comments and string literals removed, so a forbidden name mentioned in
    either is not mistaken for a call."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
    return text


def official_text_scan(text, entry, forbidden):
    """What the typical official test does: scan from the entry method's
    declaration line to end of file for any forbidden name."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and f"{entry}(int" not in lines[i]:
        i += 1
    for line in lines[i + 1:]:
        if any(f"{name}(" in line for name in forbidden):
            return True
    return False


def methods(text):
    """{name: concatenated body} for every method declared in the class."""
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


def callgraph_scan(text, entry, forbidden):
    """Forbidden calls reachable from the entry method through the class's own
    helpers. None when the class declares no entry method at all."""
    ms = methods(text)
    if entry not in ms:
        return None
    hits, seen, queue = [], {entry}, [entry]
    while queue:
        body = ms.get(queue.pop(), "")
        for call in re.findall(r"(\w+)\s*\(", body):
            if call in forbidden:
                hits.append(call)
            if call in ms and call not in seen:
                seen.add(call)
                queue.append(call)
    return sorted(set(hits))


def source_of(zf, cls):
    names = [n for n in zf.namelist()
             if n.endswith("/" + cls + ".java") or n == cls + ".java"]
    return zf.read(sorted(names)[0]).decode("utf-8", "replace") if names else None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submissions", default="submissions",
                    help="directory of student .jar files (default: submissions)")
    ap.add_argument("--entry", required=True,
                    help="the method the rule applies to, e.g. removeRange")
    ap.add_argument("--classes", required=True,
                    help="comma-separated classes to audit, e.g. StackArray,StackLinkedList")
    ap.add_argument("--forbidden", required=True,
                    help="comma-separated method names that may not be called")
    args = ap.parse_args()

    classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    forbidden = {f.strip() for f in args.forbidden.split(",") if f.strip()}

    disagreements, checked = [], 0
    for jar in sorted(pathlib.Path(args.submissions).glob("*.jar")):
        try:
            zf = zipfile.ZipFile(jar)
        except Exception as exc:
            disagreements.append({"student": jar.stem, "class": "-",
                                  "kind": "UNREADABLE ARCHIVE", "detail": str(exc)})
            continue
        for cls in classes:
            text = source_of(zf, cls)
            if text is None:
                continue
            graph = callgraph_scan(text, args.entry, forbidden)
            if graph is None:
                continue
            checked += 1
            official = official_text_scan(text, args.entry, forbidden)
            if official != bool(graph):
                disagreements.append({
                    "student": jar.stem,
                    "class": cls,
                    "kind": ("MISSED BY OFFICIAL TEST" if graph
                             else "FLAGGED BY OFFICIAL TEST ONLY"),
                    "official_text_scan_flags": official,
                    "callgraph_hits": graph,
                })

    print(json.dumps(disagreements, indent=2))
    print(f"\nchecked {checked} class file(s); {len(disagreements)} disagreement(s)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
