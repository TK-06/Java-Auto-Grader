"""List the students who changed code the assignment GAVE them.

Most weeks the starter hands students a nearly finished class and a few methods
to write. The official tests only check behaviour, so a student who "fixed" a
given method, deleted `main`, or bolted a field onto a given class can still
pass everything -- and a TA looking for exactly that has to diff a hundred
files by hand. This does the diff.

For every class in the starter folder it compares each student's own copy of
that class against the unedited given file, member by member:

  CHANGED <sig>        a given method/constructor was edited
  MISSING <sig>        a given method/constructor is gone
  FIELDS +<decl>       a class-level field was added      (informational)
  FIELDS -<decl>       a class-level field was removed    (informational)
                       (shown as written: comments gone, whitespace runs
                       collapsed to one space)
  NO SOURCE            the archive holds no <Class>.java at all
  WHOLE FILE DIFFERS   >= 80% of the given members changed or missing: almost
                       certainly another question's file, so the per-member
                       lines are suppressed rather than printed forty at a time.
                       Only for a class with at least 3 given members -- with
                       fewer, 80% is one or two methods and each is listed.

What counts as a change: any token inside a given member. What does not:
whitespace, comments, the contents of string/char literals (so a reformatted
or re-commented file is clean), and package qualifiers on types the starter
imports (an IDE that writes `iterator.Iterator` for the imported `Iterator`, or
`java.util.List` for `List`, is not a change; a qualifier the starter never
imported is). A member is identified by NAME + PARAMETER TYPES
-- `find(int)` and `find(int,int)` are different members -- so adding an overload
or a helper is never a finding, while editing the overload that was given is.
Methods named in --allow are the ones the student was told to write and are
exempt (every overload of that name in the starter).

Evidence for a human, never a score consequence: whether editing a given method
costs marks is a TA policy call. Short methods can also be rewritten into the
same tokens legitimately -- read the flagged ones before concluding anything.

Read-only: reads submissions/*.jar|*.zip and the starter folder, writes nothing,
exits 0 on a normal run. `--starter` can be ANY folder of unedited given files
(`<Class>.java` each), not only tests/starter/.

    python audit_given_code.py --allow height,rebuild --classes Tree
    python audit_given_code.py --starter C:/path/to/toStudent/src \
        --allow rebuild --diff --diff-lines 12
"""
import re
import io
import sys
import zlib
import difflib
import zipfile
import pathlib
import argparse

# Words that can precede "(" at class level without being a method name.
_CONTROL = {"if", "for", "while", "switch", "catch", "synchronized", "try",
            "do", "else", "return", "new", "throw", "assert"}
# "class Foo", "interface Foo", "enum Foo" -- a `.class` literal or `Class<?>`
# cannot match (lookbehind / case). Records need a "(" or "<" after the name so
# a method that happens to be called `record` is not mistaken for one.
_TYPE_DECL = re.compile(r"(?<![\w$.])(class|interface|enum)\s+([\w$]+)")
_RECORD_DECL = re.compile(r"(?<![\w$.])(record)\s+([\w$]+)\s*[(<]")
_ANNOTATION = re.compile(r"@(?!interface\b)[\w$]+(?:\s*\.\s*[\w$]+)*")
_IDENT_CHAR = re.compile(r"[\w$]")


# --------------------------------------------------------------------------- #
# Lexing: comments and literals out of the way so braces can be trusted
# --------------------------------------------------------------------------- #

def _literal_end(text, i):
    """Index just past the string/char literal that opens at text[i]. Java
    literals cannot span lines, so an unterminated one stops at the newline
    instead of swallowing the rest of the file."""
    quote, k, n = text[i], i + 1, len(text)
    while k < n:
        if text[k] == "\\":
            k += 2
        elif text[k] == quote:
            return k + 1
        elif text[k] == "\n":
            return k
        else:
            k += 1
    return n


def _text_block_end(text, i):
    """Index just past the text block whose opening delimiter is at text[i]."""
    k, n = i + 3, len(text)
    while k < n:
        if text[k] == "\\":
            k += 2
        elif text.startswith('"""', k):
            return k + 3
        else:
            k += 1
    return n


def _lex(text):
    """-> (code, shown, pos).

    code   comments removed (a block comment leaves one space, or the newlines it
           spanned, so tokens never glue together), string/char literal contents
           blanked to "" / ''. Line structure kept. This is what strip_noise
           returns and what every comparison runs on.
    shown  the same text with literal contents left intact -- used only to print
           a readable diff.
    pos    pos[i] is where code[i] lives in `shown`, so a slice of `code` found
           by the scanner can be shown with its real strings.
    """
    code, shown, pos = [], [], []
    n, i = len(text), 0

    def put(ch, shown_index):
        code.append(ch)
        pos.append(shown_index)

    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j          # the newline itself is kept below
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for ch in "\n" * text.count("\n", i, j) or " ":
                put(ch, len(shown))
                shown.append(ch)
            i = j
        elif c == '"' and text.startswith('"""', i):
            j = _text_block_end(text, i)
            put('"', len(shown))
            put('"', len(shown) + (j - i) - 1)
            for _ in range(text.count("\n", i, j)):
                put("\n", len(shown) + (j - i) - 1)
            shown.extend(text[i:j])
            i = j
        elif c in "\"'":
            j = _literal_end(text, i)
            put(c, len(shown))
            put(c, len(shown) + (j - i) - 1)
            shown.extend(text[i:j])
            i = j
        else:
            put(c, len(shown))
            shown.append(c)
            i += 1
    return "".join(code), "".join(shown), pos


def strip_noise(text):
    """Comments removed, string and char literal contents blanked ("" and ''),
    line structure kept. Braces left in the result are real code braces."""
    return _lex(text)[0]


# --------------------------------------------------------------------------- #
# Package qualifiers
# --------------------------------------------------------------------------- #

_PACKAGE_STMT = re.compile(r"(?<![\w$.])package\s+([\w$.\s]+?)\s*;")
_IMPORT_STMT = re.compile(r"(?<![\w$.])import\s+(?:static\s+)?([\w$.\s*]+?)\s*;")


def qualifier_prefixes(starter_text):
    """The package prefixes an IDE may write in front of a type the starter
    already imports: `import a.b.Cls;`, `import a.b.*;` and `import static
    a.b.Cls.m;` all give `a.b.`, and the starter's own `package x;` gives `x.`.
    Taken from the STARTER only -- a prefix the starter never imported is a real
    difference and must not be hidden."""
    code = strip_noise(starter_text)
    out = set()
    for m in _PACKAGE_STMT.finditer(code):
        name = re.sub(r"\s+", "", m.group(1))
        if name:
            out.add(name + ".")
    for m in _IMPORT_STMT.finditer(code):
        # drop the last segment (the class, the member, or the `*`); what is left
        # is the package, i.e. the leading lowercase segments
        segments = re.sub(r"\s+", "", m.group(1)).split(".")[:-1]
        lead = []
        for seg in segments:
            if not seg or not (seg[0].islower() or seg[0] in "_$"):
                break
            lead.append(seg)
        if lead:
            out.add(".".join(lead) + ".")
    return out


def _drop_qualifiers(code, shown, pos, prefixes):
    """Remove each `<prefix>` that is directly followed by a capital letter and
    not preceded by a word character or a dot -- `iterator.Iterator` becomes
    `Iterator`, `iterator.hasNext()` and `a.iterator.Iterator` are left alone.
    Done on the comment- and literal-free `code`, and the same characters are
    dropped from `shown` and `pos` re-mapped, so a printed diff is as neutral as
    the comparison. -> (code, shown, pos)."""
    if not prefixes:
        return code, shown, pos
    rx = re.compile(r"(?<![\w.])(?:%s)(?=[A-Z])" % "|".join(
        re.escape(p) for p in sorted(prefixes, key=len, reverse=True)))
    spans = [m.span() for m in rx.finditer(code)]
    if not spans:
        return code, shown, pos
    drop_code = bytearray(len(code))
    drop_shown = bytearray(len(shown))
    for a, b in spans:
        for i in range(a, b):
            drop_code[i] = 1
        # plain identifier characters and dots: contiguous in `shown` too
        for k in range(pos[a], pos[b - 1] + 1):
            drop_shown[k] = 1
    dropped_before = [0] * (len(shown) + 1)
    for k in range(len(shown)):
        dropped_before[k + 1] = dropped_before[k] + drop_shown[k]
    new_code, new_pos = [], []
    for i, ch in enumerate(code):
        if not drop_code[i]:
            new_code.append(ch)
            new_pos.append(pos[i] - dropped_before[pos[i]])
    new_shown = "".join(ch for k, ch in enumerate(shown) if not drop_shown[k])
    return "".join(new_code), new_shown, new_pos


# --------------------------------------------------------------------------- #
# Canonical forms
# --------------------------------------------------------------------------- #

def _squash(text):
    """Every whitespace character removed -- the form `members` and
    `field_decls` return."""
    return re.sub(r"\s+", "", text)


def _tokens(text):
    """What comparison actually uses: whitespace is not significant EXCEPT
    between two identifier characters, where one space survives. Plain
    _squash would make `new Node(1)` and `newNode(1)` -- different programs --
    compare equal."""
    out = []
    for piece in text.split():
        if out and _IDENT_CHAR.match(out[-1][-1]) and _IDENT_CHAR.match(piece[0]):
            out.append(" ")
        out.append(piece)
    return "".join(out)


# --------------------------------------------------------------------------- #
# Signatures
# --------------------------------------------------------------------------- #

def _strip_annotations(s):
    """Remove `@Name` / `@Name(...)` (balanced). `@interface` is left alone: it
    is a declaration keyword, not an annotation."""
    out, i, n = [], 0, len(s)
    while i < n:
        m = _ANNOTATION.match(s, i) if s[i] == "@" else None
        if not m:
            out.append(s[i])
            i += 1
            continue
        j = m.end()
        k = j
        while k < n and s[k].isspace():
            k += 1
        if k < n and s[k] == "(":
            depth = 0
            while k < n:
                if s[k] == "(":
                    depth += 1
                elif s[k] == ")":
                    depth -= 1
                    if depth == 0:
                        k += 1
                        break
                k += 1
            j = k
        out.append(" ")
        i = j
    return "".join(out)


def _split_params(params):
    """Split a parameter list on the commas that are not inside <...>, (...)
    or [...] -- `Map<String, List<Integer>> m` is one parameter."""
    parts, depth, cur = [], 0, []
    for ch in params:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _param_type(param):
    """The type of one parameter, whitespace removed, name dropped. `final` and
    annotations are not part of the type; `int a[]` is `int[]`; `int... n`
    keeps its varargs dots."""
    p = _strip_annotations(param)
    p = re.sub(r"(?<![\w$])final(?![\w$])", " ", p).strip()
    m = re.fullmatch(r"(.*?)\s*([\w$]+)\s*((?:\[\s*\]\s*)*)", p, re.S)
    if m and m.group(1).strip():
        return _squash(m.group(1) + m.group(3))
    return _squash(p)


def _signature_key(header, paren):
    """`name(type1,type2)` for a header whose first "(" is at `paren`. A
    constructor has no return type, so its key is simply the class name."""
    name = re.search(r"([\w$]+)\s*$", header[:paren]).group(1)
    depth, end = 0, len(header)
    for j in range(paren, len(header)):
        if header[j] == "(":
            depth += 1
        elif header[j] == ")":
            depth -= 1
            if depth == 0:
                end = j
                break
    types = [_param_type(p) for p in _split_params(header[paren + 1:end])]
    return "%s(%s)" % (name, ",".join(types))


def _classify(header):
    """What a "{" at class-body level opens. -> (kind, key):
      method  a method or constructor            key `name(types)`
      type    a class/interface/enum/record       key `class Name` etc.
      block   a (static) initializer block        key `static{}` / `{}`;
              also a record's compact constructor `P{}` and an enum constant
              body `RED{}` -- a header with no parentheses and no "="
      init    a brace group that is PART of a declaration (array initializer,
              lambda or anonymous-class field value): not a member, the
              declaration simply continues."""
    h = " ".join(_strip_annotations(header).split())
    if "=" in h:
        return "init", None
    m = _TYPE_DECL.search(h) or _RECORD_DECL.search(h)
    if m:
        return "type", "%s %s" % (m.group(1), m.group(2))
    paren = h.find("(")
    if paren >= 0:
        before = h[:paren]
        name = re.search(r"([\w$]+)\s*$", before)
        if (not name or name.group(1) in _CONTROL
                or re.search(r"(?<![\w$])new\s+[\w$.<>?, \[\]]*$", before)):
            return "init", None
        return "method", _signature_key(h, paren)
    if h in ("", "static"):
        return "block", ("static{}" if h else "{}")
    ident = re.search(r"([\w$]+)\s*$", h)
    if ident:                       # record compact constructor, enum constant body
        return "block", ident.group(1) + "{}"
    return "init", None


# --------------------------------------------------------------------------- #
# Member scanner
# --------------------------------------------------------------------------- #

def _match_brace(code, i):
    """Index of the "}" matching the "{" at code[i]; the last index when the
    file ends first (a truncated submission still yields its open member)."""
    depth = 0
    for j in range(i, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(code) - 1


def _scan(text, prefixes=()):
    """-> (members, fields).

    members  {key: (code, shown)} in source order. Two members with the same key
             (two top-level classes both defining toString(), or a duplicate
             that will not compile) are concatenated, so an edit to either shows.
    fields   [(code, shown)] -- every class-level statement ending in ";".

    `prefixes` (see qualifier_prefixes) are package qualifiers to neutralise
    before anything is split, so `iterator.Iterator` and `Iterator` come out as
    the same text -- in keys, values, fields and the diff alike.

    Method bodies, nested types and brace initialisers are consumed whole by
    brace matching on the comment- and literal-free `code`, so nothing inside
    them can be mistaken for a class-level member. A nested class is ONE opaque
    member (`class Node`): editing anything inside it is CHANGED.
    """
    code, shown, pos = _drop_qualifiers(*_lex(text), prefixes)
    n = len(code)

    def seg(a, b):
        return shown[pos[a]:pos[b - 1] + 1] if b > a else ""

    found, fields = {}, []
    in_type, paren, start, i = False, 0, 0, 0
    while i < n:
        c = code[i]
        if c == "(":
            paren += 1
        elif c == ")":
            paren = max(0, paren - 1)
        elif paren:
            pass                                # braces/semicolons inside (...)
        elif c == ";":
            stmt = code[start:i + 1]
            if in_type and stmt.strip() != ";":
                fields.append((stmt, seg(start, i + 1)))
            start = i + 1
        elif c == "{":
            kind, key = _classify(code[start:i])
            if kind == "type" and not in_type:
                in_type, start = True, i + 1    # flatten the class body
            elif kind == "init":
                i = _match_brace(code, i)       # declaration continues
            else:
                j = _match_brace(code, i)
                prev = found.get(key)
                part = (code[start:j + 1], seg(start, j + 1))
                found[key] = part if prev is None else (
                    prev[0] + "\n" + part[0], prev[1] + "\n" + part[1])
                i, start = j, j + 1
        elif c == "}":
            in_type = False
            start = i + 1
        i += 1
    return found, fields


def members(text, prefixes=()):
    """{key: value}. key is `name(type1,type2)` -- a constructor is keyed by the
    class name, `static void main(String[] args)` is `main(String[])`, parameter
    names never appear, generics/arrays/varargs do. value is the member's text
    from its signature to its closing brace with every whitespace character
    removed. `prefixes`: package qualifiers to neutralise (qualifier_prefixes of
    the starter); none by default."""
    return {k: _squash(v[0]) for k, v in _scan(text, prefixes)[0].items()}


def field_decls(text, prefixes=()):
    """Class-level statements ending in ";" (fields, with their initialisers),
    whitespace removed. Locals inside methods are not included."""
    return {_squash(c) for c, _ in _scan(text, prefixes)[1]}


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #

def _name_of(key):
    return key.split("(")[0]


# A class with fewer given members than this never collapses to WHOLE: "1 of 1
# given members changed - likely another question's file" is not evidence of
# anything, just a student who edited the one method they were given.
WHOLE_MIN_GIVEN = 3


def _fields_by_token(fields):
    """{compact comparison form: display form} in source order. The display form
    is the field as written: comments already gone, each whitespace run one
    space."""
    out = {}
    for code, shown in fields:
        out.setdefault(_tokens(code), " ".join(shown.split()))
    return out


def compare(starter_text, student_text, allow):
    """Findings for one student's copy of one class: [(kind, detail)], kind in
    CHANGED, MISSING, FIELDS+, FIELDS-, WHOLE. Members the student added are
    never findings. Allowed names are exempt, every overload of them. Package
    qualifiers the STARTER imports are neutral on both sides."""
    prefixes = qualifier_prefixes(starter_text)
    s_members, s_fields = _scan(starter_text, prefixes)
    t_members, t_fields = _scan(student_text, prefixes)
    allow = set(allow)

    given = [k for k in s_members if _name_of(k) not in allow]
    bad = []
    for key in given:
        if key not in t_members:
            bad.append(("MISSING", key))
        elif _tokens(s_members[key][0]) != _tokens(t_members[key][0]):
            bad.append(("CHANGED", key))

    # 80% or more of what was given is gone/different: not "a student who edited
    # a method" but a different file entirely -- once there are enough given
    # members for a percentage to mean anything. Integer arithmetic, no float edge.
    if len(given) >= WHOLE_MIN_GIVEN and len(bad) * 5 >= len(given) * 4:
        return [("WHOLE", "%d of %d given members changed or missing"
                 % (len(bad), len(given)))]

    s_decls = _fields_by_token(s_fields)
    t_decls = _fields_by_token(t_fields)
    out = list(bad)
    out += [("FIELDS+", shown) for tok, shown in t_decls.items() if tok not in s_decls]
    out += [("FIELDS-", shown) for tok, shown in s_decls.items() if tok not in t_decls]
    return out


# --------------------------------------------------------------------------- #
# Archives
# --------------------------------------------------------------------------- #

# What a damaged, encrypted or truncated archive can raise. Deliberately a list
# of types, not `except Exception`: a bug in the splitter must stay visible.
_ARCHIVE_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError,
                   NotImplementedError, EOFError, zlib.error)


_BOM = chr(0xFEFF)  # a UTF-8 BOM some editors prepend to exported source


def _decode(data):
    return data.decode("utf-8", "replace").lstrip(_BOM)


def _java_entry(zf, cls):
    """The archive entry for <cls>.java, wherever it sits. Two copies resolve to
    the alphabetically first path -- the same rule grade.py applies, so the audit
    reads the file that was graded."""
    want = cls + ".java"
    hits = [n for n in zf.namelist()
            if n.replace("\\", "/").rsplit("/", 1)[-1] == want]
    return min(hits) if hits else None


def source_of(zf, cls):
    """Text of <cls>.java in the archive, or None. When the archive itself holds
    none, each .jar/.zip inside it is searched ONE level deep (a zip that wraps
    the exported jar); an unreadable nested archive is skipped."""
    name = _java_entry(zf, cls)
    if name is not None:
        return _decode(zf.read(name))
    for inner in sorted(n for n in zf.namelist()
                        if n.lower().endswith((".jar", ".zip"))):
        try:
            with zipfile.ZipFile(io.BytesIO(zf.read(inner))) as nested:
                name = _java_entry(nested, cls)
                if name is not None:
                    return _decode(nested.read(name))
        except _ARCHIVE_ERRORS:
            continue
    return None


# --------------------------------------------------------------------------- #
# Diff and report
# --------------------------------------------------------------------------- #

def _display_lines(shown):
    return [" ".join(line.split()) for line in shown.splitlines() if line.strip()]


def member_diff(starter_text, student_text, key, limit):
    """Unified diff of one member, starter first. Lines are whitespace-normalised
    and comment-free but keep their real string contents. No ---/+++ header
    (the key names what is being diffed) and no @@ line numbers (they would be
    numbered inside the member, which means nothing to a reader): separate hunks
    get a `...` line instead. At most `limit` lines, then a count of the rest."""
    prefixes = qualifier_prefixes(starter_text)
    old = _display_lines(_scan(starter_text, prefixes)[0][key][1])
    new = _display_lines(_scan(student_text, prefixes)[0][key][1])
    body = list(difflib.unified_diff(old, new, lineterm="", n=1))[2:]
    out = []
    for line in body:
        if line.startswith("@@"):
            if out:
                out.append("...")
            continue
        out.append(line if len(line) <= 200 else line[:197] + "...")
    if len(out) > limit:
        extra = len(out) - limit
        out = out[:limit] + ["... %d more diff line%s" % (extra, "" if extra == 1 else "s")]
    return out


def _label(kind, detail):
    if kind == "FIELDS+":
        return "FIELDS +" + detail
    if kind == "FIELDS-":
        return "FIELDS -" + detail
    if kind == "WHOLE":
        return "WHOLE FILE DIFFERS (%s - likely another question's file)" % detail
    return "%s %s" % (kind, detail)


def _group(kind):
    return {"FIELDS+": "FIELDS", "FIELDS-": "FIELDS",
            "WHOLE": "WHOLE FILE DIFFERS"}.get(kind, kind)


_GROUP_ORDER = ["CHANGED", "MISSING", "FIELDS", "NO SOURCE", "WHOLE FILE DIFFERS",
                "UNREADABLE ARCHIVE"]


def _audit_archive(path, classes, starters, allow, show_diff, diff_lines):
    """[(class, kind, detail, diff_lines)] for one archive."""
    out = []
    try:
        with zipfile.ZipFile(path) as zf:
            for cls in classes:
                text = source_of(zf, cls)
                if text is None:
                    out.append((cls, "NO SOURCE",
                                "(no %s.java in the archive)" % cls, []))
                    continue
                for kind, detail in compare(starters[cls], text, allow):
                    extra = []
                    if show_diff and kind == "CHANGED":
                        extra = member_diff(starters[cls], text, detail, diff_lines)
                    out.append((cls, kind, detail, extra))
    except _ARCHIVE_ERRORS as exc:
        return [("", "UNREADABLE ARCHIVE",
                 "(%s: %s)" % (type(exc).__name__, exc), [])]
    return out


def build_report(submissions_dir, starter_dir, allow, classes=None,
                 show_diff=False, diff_lines=20):
    """The whole report as one string. Exits (SystemExit with a message) when
    the starter folder is missing or holds no .java, when a requested class is
    not in it, or when the submissions folder is missing -- an empty report for
    a mistyped path would read as "nobody changed anything"."""
    starter_dir = pathlib.Path(starter_dir)
    submissions_dir = pathlib.Path(submissions_dir)
    if not starter_dir.is_dir():
        sys.exit("audit_given_code: starter folder not found: %s" % starter_dir)
    given_files = {p.stem: p for p in sorted(starter_dir.glob("*.java"))}
    if not given_files:
        sys.exit("audit_given_code: no .java files in starter folder %s"
                 % starter_dir)
    if classes:
        classes = list(dict.fromkeys(classes))
        unknown = [c for c in classes if c not in given_files]
        if unknown:
            sys.exit("audit_given_code: no %s in starter folder %s (it has: %s)"
                     % (", ".join(c + ".java" for c in unknown), starter_dir,
                        ", ".join(sorted(given_files))))
    else:
        classes = sorted(given_files)
    if not submissions_dir.is_dir():
        sys.exit("audit_given_code: submissions folder not found: %s"
                 % submissions_dir)

    allow = set(allow)
    starters = {c: _decode(given_files[c].read_bytes()) for c in classes}
    width = max(len(c) for c in classes)

    class_lines, all_names = [], set()
    for cls in classes:
        keys = list(_scan(starters[cls], qualifier_prefixes(starters[cls]))[0])
        all_names.update(_name_of(k) for k in keys)
        exempt = [k for k in keys if _name_of(k) in allow]
        given = len(keys) - len(exempt)
        class_lines.append("  %s  %d given member%s audited%s" % (
            cls.ljust(width), given, "" if given == 1 else "s",
            "; exempt: " + ", ".join(exempt) if exempt else ""))

    archives = sorted((p for p in submissions_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in (".jar", ".zip")),
                      key=lambda p: (p.stem, p.suffix))

    counts, blocks, untouched = {}, [], 0
    for path in archives:
        found = _audit_archive(path, classes, starters, allow, show_diff, diff_lines)
        if not found:
            untouched += 1
            continue
        block = [path.stem]
        for cls, kind, detail, extra in found:
            counts[_group(kind)] = counts.get(_group(kind), 0) + 1
            # an unreadable archive is not about one class, so it has no class column
            lead = "  %s  " % cls.ljust(width) if cls else "  "
            block.append(lead + _label(kind, detail))
            block.extend("  " + " " * width + "    " + line for line in extra)
        blocks.append(block)

    out = [
        "audit_given_code - changes to code the assignment gave "
        "(evidence for a human, never a score)",
        "starter:      %s" % starter_dir,
        "submissions:  %s (%d archive%s)" % (
            submissions_dir, len(archives), "" if len(archives) == 1 else "s"),
        "allowed:      %s" % (", ".join(sorted(allow)) or "(none)"),
        "classes:",
        *class_lines,
    ]
    unmatched = sorted(a for a in allow if a not in all_names)
    if unmatched:
        out.append("note: --allow name(s) not found in any audited starter class: "
                   + ", ".join(unmatched))
    summary = ", ".join("%s %d" % (g, counts[g]) for g in _GROUP_ORDER if g in counts)
    out += ["", "untouched: %d" % untouched,
            "with findings: %d%s" % (len(blocks), " (%s)" % summary if summary else "")]
    for block in blocks:
        out += [""] + block
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--starter", default="tests/starter",
                    help="folder of unedited given files, one <Class>.java each "
                         "(default: tests/starter)")
    ap.add_argument("--allow", required=True,
                    help="comma-separated method names the student was told to "
                         "write; every other method/constructor in the starter is "
                         "audited. Matches every overload of the name. Pass \"\" "
                         "to audit everything")
    ap.add_argument("--submissions", default="submissions",
                    help="directory of student .jar/.zip files (default: submissions)")
    ap.add_argument("--classes", default="",
                    help="comma-separated classes to audit (default: every .java "
                         "in --starter)")
    ap.add_argument("--diff", action="store_true",
                    help="print a unified diff under each CHANGED member")
    ap.add_argument("--diff-lines", type=int, default=20,
                    help="cap each --diff at this many lines (default: 20)")
    args = ap.parse_args(argv)
    if args.diff_lines < 1:
        ap.error("--diff-lines must be at least 1")

    def names(s):
        return [x.strip() for x in s.split(",") if x.strip()]

    report = build_report(args.submissions, args.starter, set(names(args.allow)),
                          names(args.classes) or None, args.diff, args.diff_lines)
    # Student text can hold any script; a redirected Windows stdout is often
    # cp1252, and one Thai string must not turn a finished audit into a traceback.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(errors="replace")
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
