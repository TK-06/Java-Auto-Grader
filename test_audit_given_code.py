"""Tests for audit_given_code.py.

Everything here is synthetic: an invented `Tree` class and ten-digit-style ids
like 10000001. No real student work or answer-key text, and nothing under
submissions/, results/ or tests/ is read - the archive tests build their own
jars in a temporary directory.
"""
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from audit_given_code import (build_report, compare, field_decls, main,
                              members, qualifier_prefixes, source_of,
                              strip_noise)

STARTER = '''package t;
import java.util.List;
public class Tree {
    public Node root;
    public int size;
    public Tree(Node root, int size) { this.root = root; this.size = size; }
    public boolean isEmpty() { return size == 0; }   // given
    public Node find(int v) {
        Node temp = root;
        while (temp != null && temp.data != v) { temp = v < temp.data ? temp.left : temp.right; }
        return temp;
    }
    public int height() {
        //TODO: implement this method.
        return 0;
    }
    public void rebuild() {
        //TODO
    }
    public static void main(String[] args) {
        System.out.println("}" + '{');   // braces in a string and a char
        Tree t = new Tree(null, 0);
    }
}
'''

ALLOW = {"height", "rebuild"}

HEIGHT_BODY = '''    public int height() {
        //TODO: implement this method.
        return 0;
    }
'''

MAIN_BLOCK = '''    public static void main(String[] args) {
        System.out.println("}" + '{');   // braces in a string and a char
        Tree t = new Tree(null, 0);
    }
'''


def edit(old, new, text=STARTER):
    """str.replace that fails loudly if the fixture drifted from the test."""
    assert old in text, "fixture no longer contains %r" % old
    return text.replace(old, new, 1)


class TestStripNoise(unittest.TestCase):
    def test_comments_removed_and_line_structure_kept(self):
        src = "a // gone }\nb /* x\n y } */ c\nd"
        out = strip_noise(src)
        self.assertNotIn("gone", out)
        self.assertNotIn("}", out)
        self.assertEqual(out.count("\n"), src.count("\n"))
        self.assertEqual(out.split(), ["a", "b", "c", "d"])

    def test_string_and_char_contents_blanked(self):
        src = 'x = "a}b" + \'{\' + "q\\"r}";'
        self.assertEqual(strip_noise(src), 'x = "" + \'\' + "";')

    def test_escaped_char_literal_and_escaped_backslash(self):
        self.assertEqual(strip_noise("c = '\\''; d = 1;"), "c = ''; d = 1;")
        self.assertEqual(strip_noise('s = "\\\\"; t = 1;'), 's = ""; t = 1;')

    def test_comment_markers_inside_strings_are_not_comments(self):
        self.assertEqual(strip_noise('String u = "http://x"; int a = 1;'),
                         'String u = ""; int a = 1;')

    def test_apostrophe_in_a_comment_does_not_open_a_char_literal(self):
        out = strip_noise("int a; // don't\nint b; /* it's */ int c;")
        self.assertIn("int b;", out)
        self.assertIn("int c;", out)

    def test_text_block_contents_blanked_and_lines_kept(self):
        src = 's = """\n  a } b\n  """; int x;'
        out = strip_noise(src)
        self.assertNotIn("}", out)
        self.assertIn("int x;", out)
        self.assertEqual(out.count("\n"), src.count("\n"))

    def test_block_comment_between_tokens_does_not_glue_them(self):
        self.assertEqual(strip_noise("int/**/x;").split(), ["int", "x;"])


class TestMembers(unittest.TestCase):
    def test_every_member_of_the_starter_is_found(self):
        self.assertEqual(
            list(members(STARTER)),
            ["Tree(Node,int)", "isEmpty()", "find(int)", "height()",
             "rebuild()", "main(String[])"])

    def test_constructor_is_keyed_by_class_name_plus_param_types(self):
        # No return type: a "modifier Type name(" pattern skips this silently.
        self.assertIn("Tree(Node,int)", members(STARTER))

    def test_package_private_constructor_without_modifier_is_a_member(self):
        self.assertIn("Tree(int)", members("class Tree { Tree(int n) { } }"))

    def test_static_main_is_keyed_main_string_array(self):
        self.assertIn("main(String[])", members(STARTER))
        self.assertIn("main(String...)", members(
            "class A { public static void main(String... args) { } }"))

    def test_overloads_are_distinct_members(self):
        src = "class A { int h() { return 0; } int h(Node n) { return 1; } }"
        self.assertEqual(list(members(src)), ["h()", "h(Node)"])

    def test_parameter_names_never_appear_in_the_key(self):
        a = members("class A { void f(int count, String label) { } }")
        b = members("class A { void f(int n, String s) { } }")
        self.assertEqual(list(a), ["f(int,String)"])
        self.assertEqual(list(b), ["f(int,String)"])

    def test_generics_arrays_and_varargs_survive_in_the_key(self):
        src = """class C {
            void a(List<Integer> xs, Map<String, List<Integer>> m) { }
            void b(int[] a, String[][] g) { }
            void c(int... n) { }
            void d(final int a[]) { }
            <T extends Comparable<T>> T e(T x, Comparator<? super T> c) { return x; }
        }"""
        self.assertEqual(list(members(src)), [
            "a(List<Integer>,Map<String,List<Integer>>)",
            "b(int[],String[][])",
            "c(int...)",
            "d(int[])",
            "e(T,Comparator<?superT>)",
        ])

    def test_braces_in_strings_chars_and_comments_do_not_shift_depth(self):
        src = '''class C {
            void a() { String s = "}"; char c = '{'; // }
                /* { */ int x = 0; }
            void b() { }
        }'''
        self.assertEqual(list(members(src)), ["a()", "b()"])

    def test_value_is_whitespace_free_and_comment_free(self):
        # "given" comment on that line must not survive into the value
        self.assertEqual(members(STARTER)["isEmpty()"],
                         "publicbooleanisEmpty(){returnsize==0;}")

    def test_value_runs_from_signature_to_closing_brace_only(self):
        v = members(STARTER)["find(int)"]
        self.assertTrue(v.startswith("publicNodefind(intv){"))
        self.assertTrue(v.endswith("returntemp;}"))
        self.assertNotIn("height", v)

    def test_annotations_do_not_confuse_the_key(self):
        src = '''class C {
            @Override public String toString() { return ""; }
            @SuppressWarnings("unchecked") public <T> T[] make(Class<T> c) { return null; }
            @A(x = {1, 2}) void g() { }
        }'''
        self.assertEqual(list(members(src)),
                         ["toString()", "make(Class<T>)", "g()"])

    def test_throws_clause_does_not_break_the_key(self):
        src = "class C { void f(int a) throws IOException, X { } }"
        self.assertEqual(list(members(src)), ["f(int)"])

    def test_nested_class_is_one_opaque_member(self):
        src = '''class C {
            static class N { int v; N(int v) { this.v = v; } int get() { return v; } }
            void f() { }
        }'''
        self.assertEqual(list(members(src)), ["class N", "f()"])

    def test_brace_initialisers_are_not_members(self):
        src = '''class C {
            int[] a = {1, 2};
            Runnable r = () -> { run(); };
            Object o = new Object() { void x() { } };
            void f() { }
        }'''
        self.assertEqual(list(members(src)), ["f()"])

    def test_static_initializer_block_is_a_member(self):
        src = "class C { static { init(); } void f() { } }"
        self.assertEqual(list(members(src)), ["static{}", "f()"])

    def test_record_compact_constructor_does_not_corrupt_the_next_key(self):
        # `P { ... }` has no parentheses: it must not leak into f()'s header and
        # make the key come out as check(x).
        src = "record P(int x) { P { check(x); } int f() { return x; } }"
        self.assertEqual(list(members(src)), ["P{}", "f()"])

    def test_enum_constant_bodies_do_not_corrupt_the_next_key(self):
        src = "enum E { A { void h() { } }, B { }; void g() { } }"
        self.assertEqual(list(members(src)), ["A{}", "B{}", "g()"])

    def test_two_top_level_classes_share_one_namespace(self):
        src = "class Node { Node(int v) { } }\npublic class Tree { Tree() { } }"
        self.assertEqual(list(members(src)), ["Node(int)", "Tree()"])

    def test_truncated_file_does_not_raise_and_keeps_open_member(self):
        got = members("class C { void a() { } void b() { if (x) {")
        self.assertIn("a()", got)
        self.assertIn("b()", got)

    def test_stray_closing_brace_does_not_raise(self):
        got = members("class C { void a() { } } } void b() { }")
        self.assertIn("a()", got)
        self.assertIn("b()", got)

    def test_empty_text_has_no_members(self):
        self.assertEqual(members(""), {})


class TestFieldDecls(unittest.TestCase):
    def test_class_level_fields_found_and_imports_ignored(self):
        self.assertEqual(field_decls(STARTER),
                         {"publicNoderoot;", "publicintsize;"})

    def test_locals_inside_methods_are_not_fields(self):
        got = field_decls(STARTER)
        self.assertFalse(any("temp" in d or "newTree" in d for d in got))

    def test_initialised_field_is_kept_whole(self):
        self.assertEqual(field_decls("class C { private int[] a = {1, 2}; }"),
                         {"privateint[]a={1,2};"})

    def test_stray_semicolon_after_a_method_is_not_a_field(self):
        self.assertEqual(field_decls("class C { void f() { }; int x; }"),
                         {"intx;"})


class TestCompare(unittest.TestCase):
    def test_identical_text_is_clean(self):
        self.assertEqual(compare(STARTER, STARTER, ALLOW), [])

    def test_reformatting_is_not_a_change(self):
        student = edit("return size == 0;", "return size==0\n   ;")
        student = edit("while (temp != null && temp.data != v) {",
                       "while(temp!=null&&temp.data!=v)\n        {", student)
        self.assertEqual(compare(STARTER, student, ALLOW), [])

    def test_if_spacing_is_not_a_change(self):
        a = "class C { int f(Node n) { if (n == null) { return 0; } return 1; } int g() { return 2; } }"
        b = "class C { int f(Node n) { if(n== null){ return 0;}\n return 1; } int g() { return 2; } }"
        self.assertEqual(compare(a, b, set()), [])

    def test_comments_and_string_contents_are_not_a_change(self):
        student = edit("return temp;", "// my note }\n        return temp; /* {{ */")
        student = edit('println("}"', 'println("anything else"', student)
        self.assertEqual(compare(STARTER, student, ALLOW), [])

    def test_single_token_change_inside_a_given_method_is_changed(self):
        student = edit("return size == 0;", "return size <= 0;")
        self.assertEqual(compare(STARTER, student, ALLOW),
                         [("CHANGED", "isEmpty()")])

    def test_less_than_to_less_or_equal_inside_find_is_changed(self):
        student = edit("v < temp.data", "v <= temp.data")
        self.assertEqual(compare(STARTER, student, ALLOW),
                         [("CHANGED", "find(int)")])

    def test_glued_tokens_are_a_change(self):
        # "new Node(1)" -> "newNode(1)" is a different program even though the
        # two strings are identical once every space is deleted.
        pad = " void p() { } void q() { } void r() { } void s() { }"
        a = "class C { Object f() { return new Node(1); }" + pad + " }"
        b = "class C { Object f() { return newNode(1); }" + pad + " }"
        self.assertEqual(compare(a, b, set()), [("CHANGED", "f()")])

    def test_added_helper_and_added_overload_of_allowed_name_is_no_finding(self):
        student = edit(HEIGHT_BODY, '''    public int height() { return height(root); }
    private int height(Node n) { return n == null ? 0 : 1 + Math.max(height(n.left), height(n.right)); }
    private void helper() { }
''')
        self.assertEqual(compare(STARTER, student, ALLOW), [])

    def test_edited_find_is_changed_even_with_an_added_overload(self):
        student = edit("return temp;", "return null;")
        student = edit(HEIGHT_BODY, HEIGHT_BODY +
                       "    public Node find(int a, int b) { return null; }\n", student)
        self.assertEqual(compare(STARTER, student, ALLOW),
                         [("CHANGED", "find(int)")])

    def test_deleted_main_is_missing(self):
        student = edit(MAIN_BLOCK, "")
        self.assertEqual(compare(STARTER, student, ALLOW),
                         [("MISSING", "main(String[])")])

    def test_modified_constructor_is_changed(self):
        student = edit("this.size = size;", "this.size = 1;")
        self.assertEqual(compare(STARTER, student, ALLOW),
                         [("CHANGED", "Tree(Node,int)")])

    def test_added_field_is_reported_as_fields_plus(self):
        student = edit("public int size;", "public int size;\n    private int cache;")
        self.assertEqual(compare(STARTER, student, ALLOW),
                         [("FIELDS+", "private int cache;")])

    def test_removed_field_is_reported_as_fields_minus(self):
        student = edit("    public int size;\n", "")
        self.assertEqual(compare(STARTER, student, ALLOW),
                         [("FIELDS-", "public int size;")])

    def test_allowed_method_edit_or_deletion_is_ignored(self):
        student = edit(HEIGHT_BODY, "    public int height() { return 7; }\n")
        student = edit("    public void rebuild() {\n        //TODO\n    }\n", "", student)
        self.assertEqual(compare(STARTER, student, ALLOW), [])

    def test_allowed_name_exempts_every_starter_overload_of_it(self):
        pad = " void p() { } void q() { } void r() { } void s() { }"
        a = "class C { int h() { return 0; } int h(int x) { return 0; }" + pad + " }"
        b = "class C { int h() { return 1; } int h(int x) { return 2; }" + pad + " }"
        self.assertEqual(compare(a, b, {"h"}), [])
        self.assertEqual(sorted(compare(a, b, set())),
                         [("CHANGED", "h()"), ("CHANGED", "h(int)")])

    def test_findings_are_in_starter_order_members_before_fields(self):
        student = edit("return size == 0;", "return false;")
        student = edit("return temp;", "return null;", student)
        student = edit("public int size;", "public int size;\n    int extra;", student)
        self.assertEqual(compare(STARTER, student, ALLOW), [
            ("CHANGED", "isEmpty()"),
            ("CHANGED", "find(int)"),
            ("FIELDS+", "int extra;"),
        ])

    def test_whole_file_differs_at_eighty_percent_collapses_to_one_finding(self):
        # allow only height -> 5 given members; change 4 of them (80%).
        student = edit("return size == 0;", "return false;")
        student = edit("return temp;", "return null;", student)
        student = edit("this.size = size;", "this.size = 9;", student)
        student = edit("//TODO\n", "int z = 1;\n", student)
        got = compare(STARTER, student, {"height"})
        self.assertEqual([k for k, _ in got], ["WHOLE"])

    def test_below_eighty_percent_lists_each_member(self):
        student = edit("return size == 0;", "return false;")
        student = edit("return temp;", "return null;", student)
        student = edit("this.size = size;", "this.size = 9;", student)
        got = compare(STARTER, student, {"height"})
        self.assertEqual([k for k, _ in got], ["CHANGED"] * 3)

    def test_another_questions_file_is_one_finding_not_forty(self):
        other = '''package q;
        public class Queue {
            private int[] data = new int[10];
            private int head;
            public Queue() { }
            public void enqueue(int v) { data[head++] = v; }
            public int dequeue() { return data[--head]; }
            public boolean isEmpty() { return head == 0; }
            public static void main(String[] args) { new Queue(); }
        }'''
        got = compare(STARTER, other, ALLOW)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "WHOLE")

    def test_whole_file_finding_suppresses_field_lines_too(self):
        got = compare(STARTER, "class X { int unrelated; }", ALLOW)
        self.assertEqual([k for k, _ in got], ["WHOLE"])

    def test_nothing_given_means_never_whole(self):
        starter = "class C { void a() { } }"
        self.assertEqual(compare(starter, "class C { }", {"a"}), [])

    def test_never_raises_on_mangled_source(self):
        # A broken submission is exactly what an audit meets; a traceback would
        # end the whole run. Deterministic: same mangling every time.
        import random
        rng = random.Random(20261013)
        junk = ['"', "'", "{", "}", "(", ")", ";", "/*", "*/", "//", '"""', "@",
                "<", ">", "=", "class ", "enum ", "record ", "\\", "->", "\n"]
        for _ in range(150):
            text = STARTER
            for _ in range(rng.randint(1, 6)):
                at = rng.randrange(len(text) + 1)
                if rng.random() < 0.5:
                    text = text[:at] + text[at + rng.randint(1, 40):]
                else:
                    text = text[:at] + rng.choice(junk) + text[at:]
            self.assertIsInstance(compare(STARTER, text, ALLOW), list)
            self.assertIsInstance(members(text), dict)
            self.assertIsInstance(field_decls(text), set)

    def test_changed_nested_class_is_reported_as_that_class(self):
        a = "class C { static class N { int v = 1; } void f() { } }"
        b = "class C { static class N { int v = 2; } void f() { } }"
        self.assertEqual(compare(a, b, set()), [("CHANGED", "class N")])



# --------------------------------------------------------------------------- #
# Follow-up: qualifier-neutral comparison, WHOLE minimum, field display
# --------------------------------------------------------------------------- #

QSTARTER = '''package bst;
import iterator.Iterator;
import iterator.TreeIterator;
import java.util.*;
import static java.lang.Math.max;
public class Q {
    private Iterator<Integer> it;
    public Q() { it = new TreeIterator<>(); }
    public int findMin() { Iterator<Integer> i = new TreeIterator<>(); return i.next(); }
    public int findMax() { return max(1, 2); }
    public void insert(int v) { List<Integer> xs = new ArrayList<Integer>(); xs.add(v); }
    public void put(List<Integer> xs) { xs.clear(); }
    public boolean find(int v) { return v == 0; }
    public static void main(String[] args) { Iterator<Integer> i = new TreeIterator<>(); }
}
'''

QUALIFIERS = {"Iterator": "iterator.", "TreeIterator": "iterator.",
              "List": "java.util.", "ArrayList": "java.util."}


def qualify(text, qualifiers=None):
    """Rewrite `text` the way an IDE that drops the imports does: every use of an
    imported type is written by its full name and the import lines are gone."""
    for name, prefix in (qualifiers or QUALIFIERS).items():
        text = re.sub(r"(?<![\w.])%s\b" % name, prefix + name, text)
    return re.sub(r"^import (?!static).*\n", "", text, flags=re.M)


class TestQualifierPrefixes(unittest.TestCase):
    def test_imports_static_imports_wildcards_and_own_package(self):
        self.assertEqual(qualifier_prefixes(QSTARTER),
                         {"bst.", "iterator.", "java.util.", "java.lang."})

    def test_nested_class_import_gives_the_package_only(self):
        self.assertEqual(qualifier_prefixes("import a.b.Outer.Inner;"), {"a.b."})

    def test_comments_and_strings_do_not_make_prefixes(self):
        src = '// import x.y.Z;\n/* import p.q.R; */\nclass C { String s = "import m.n.O;"; }'
        self.assertEqual(qualifier_prefixes(src), set())

    def test_default_package_and_no_imports_give_nothing(self):
        self.assertEqual(qualifier_prefixes("class C { }"), set())

    def test_package_and_import_on_one_line(self):
        self.assertEqual(qualifier_prefixes("package a.b; import c.D;"), {"a.b.", "c."})


class TestQualifierNeutral(unittest.TestCase):
    PAD = " void p() { } void q() { } void r() { }"

    def test_fully_qualified_student_compares_clean(self):
        self.assertEqual(compare(QSTARTER, qualify(QSTARTER), set()), [])

    def test_one_real_change_in_a_qualified_file_is_changed_alone(self):
        student = qualify(QSTARTER).replace("return v == 0;", "return v != 0;")
        self.assertEqual(compare(QSTARTER, student, set()),
                         [("CHANGED", "find(int)")])

    def test_parameter_type_keys_match_across_qualification(self):
        got = members(qualify(QSTARTER), qualifier_prefixes(QSTARTER))
        self.assertIn("put(List<Integer>)", got)

    def test_prefix_the_starter_never_imported_is_left_alone(self):
        starter = ("import iterator.Iterator;\nclass A { boolean same(Object a, Object b)"
                   " { return Objects.equals(a, b); }" + self.PAD + " }")
        student = starter.replace("return Objects.equals", "return java.util.Objects.equals")
        self.assertEqual(compare(starter, student, set()),
                         [("CHANGED", "same(Object,Object)")])

    def test_variable_named_like_the_package_with_lowercase_member_is_untouched(self):
        src = "import iterator.Iterator;\nclass A { boolean more() { return iterator.hasNext(); } }"
        got = members(src, qualifier_prefixes(src))["more()"]
        self.assertIn("iterator.hasNext()", got)

    def test_a_real_edit_to_that_call_is_still_a_change(self):
        starter = ("import iterator.Iterator;\nclass A { boolean more()"
                   " { return iterator.hasNext(); }" + self.PAD + " }")
        student = starter.replace("iterator.hasNext()", "iterator.next()")
        self.assertEqual(compare(starter, student, set()), [("CHANGED", "more()")])

    def test_qualifier_preceded_by_a_dot_is_not_removed(self):
        src = "import iterator.Iterator;\nclass A { Object f() { return outer.iterator.Iterator.X; } }"
        got = members(src, qualifier_prefixes(src))["f()"]
        self.assertIn("outer.iterator.Iterator.X", got)

    def test_starters_own_package_is_neutral(self):
        starter = "package bst;\nclass A { Object f() { return new Node(); }" + self.PAD + " }"
        student = starter.replace("new Node()", "new bst.Node()")
        self.assertEqual(compare(starter, student, set()), [])

    def test_longer_and_shorter_prefixes_both_apply(self):
        starter = ("import java.util.List;\nimport java.util.function.Function;\n"
                   "class A { Object f(Function<List<Integer>, Integer> g) { return g; }"
                   + self.PAD + " }")
        student = starter.replace(
            "Function<List<Integer>, Integer>",
            "java.util.function.Function<java.util.List<Integer>, Integer>")
        self.assertEqual(compare(starter, student, set()), [])

    def test_qualified_field_declaration_compares_clean(self):
        starter = "import java.util.List;\nclass A { private List<Integer> xs;" + self.PAD + " }"
        student = starter.replace("private List<Integer>", "private java.util.List<Integer>")
        self.assertEqual(compare(starter, student, set()), [])

    def test_default_is_no_neutralisation_for_members_on_its_own(self):
        got = members("class A { Object f() { return new a.b.C(); } }")
        self.assertIn("a.b.C", got["f()"])


class TestWholeFileMinimum(unittest.TestCase):
    def test_two_given_members_both_changed_are_listed_not_collapsed(self):
        a = "class C { int a() { return 1; } int b() { return 2; } }"
        b = "class C { int a() { return 9; } int b() { return 8; } }"
        self.assertEqual(compare(a, b, set()),
                         [("CHANGED", "a()"), ("CHANGED", "b()")])

    def test_one_given_member_changed_is_a_plain_change(self):
        a = "class C { int a() { return 1; } }"
        b = "class C { int a() { return 9; } }"
        self.assertEqual(compare(a, b, set()), [("CHANGED", "a()")])

    def test_two_given_members_both_missing_are_listed(self):
        a = "class C { int a() { return 1; } int b() { return 2; } }"
        self.assertEqual(compare(a, "class C { }", set()),
                         [("MISSING", "a()"), ("MISSING", "b()")])

    def test_three_given_members_all_changed_do_collapse(self):
        a = "class C { int a() { return 1; } int b() { return 2; } int c() { return 3; } }"
        b = "class C { int a() { return 9; } int b() { return 8; } int c() { return 7; } }"
        got = compare(a, b, set())
        self.assertEqual([k for k, _ in got], ["WHOLE"])
        self.assertIn("3 of 3", got[0][1])


class TestFieldDisplay(unittest.TestCase):
    PAD = " void p() { } void q() { } void r() { }"

    def added(self, field):
        got = compare("class C {" + self.PAD + " }",
                      "class C { " + field + self.PAD + " }", set())
        return [d for k, d in got if k == "FIELDS+"]

    def test_generic_field_is_shown_as_written(self):
        self.assertEqual(self.added("ArrayList<Integer> numbers = new ArrayList<>();"),
                         ["ArrayList<Integer> numbers = new ArrayList<>();"])

    def test_modifier_and_array_fields_are_shown_as_written(self):
        self.assertEqual(self.added("private int index;"), ["private int index;"])
        self.assertEqual(self.added("int[] a = new int[size];"),
                         ["int[] a = new int[size];"])

    def test_whitespace_runs_collapse_to_one_space(self):
        self.assertEqual(self.added("ArrayList<Integer>   numbers\n        =\tnew ArrayList<>();"),
                         ["ArrayList<Integer> numbers = new ArrayList<>();"])

    def test_comments_are_stripped_from_the_display(self):
        self.assertEqual(self.added("/* cache */ // note\n    private int n;"),
                         ["private int n;"])

    def test_string_initialiser_keeps_its_text(self):
        self.assertEqual(self.added('String s = "hi there";'), ['String s = "hi there";'])

    def test_removed_field_is_shown_as_the_starter_wrote_it(self):
        starter = "class C { private   int\n   size;" + self.PAD + " }"
        got = compare(starter, "class C {" + self.PAD + " }", set())
        self.assertEqual(got, [("FIELDS-", "private int size;")])

    def test_comparison_still_ignores_formatting(self):
        starter = "class C { int x = 1;" + self.PAD + " }"
        student = "class C { int   x=1 ;" + self.PAD + " }"
        self.assertEqual(compare(starter, student, set()), [])

    def test_public_field_decls_keep_the_compact_form(self):
        self.assertEqual(field_decls("class C { ArrayList<Integer> numbers = new ArrayList<>(); }"),
                         {"ArrayList<Integer>numbers=newArrayList<>();"})


# --------------------------------------------------------------------------- #
# Task 2: archives, report, CLI
# --------------------------------------------------------------------------- #

SCRIPT = Path(__file__).with_name("audit_given_code.py")
# "hello" in Thai, built from code points so this file stays ASCII
THAI = "".join(chr(c) for c in (0x0E2A, 0x0E27, 0x0E31, 0x0E2A, 0x0E14, 0x0E35))


def make_zip(path, files):
    """files: {entry name: str | bytes}; path may be a path or a file object."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def zip_bytes(files):
    buf = io.BytesIO()
    make_zip(buf, files)
    return buf.getvalue()


def tree_snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in Path(root).rglob("*"))


def stripped_lines(text):
    return [l.strip() for l in text.splitlines()]


class TestSourceOf(unittest.TestCase):
    def open(self, files):
        return zipfile.ZipFile(io.BytesIO(zip_bytes(files)))

    def test_found_by_basename_anywhere_in_the_archive(self):
        zf = self.open({"deep/er/src/t/Tree.java": "class Tree {}"})
        self.assertEqual(source_of(zf, "Tree"), "class Tree {}")

    def test_basename_must_match_exactly(self):
        zf = self.open({"MyTree.java": "x", "Tree.java.bak": "x", "._Tree.java": "x",
                        "TreeNode.java": "x", "src/Tree.class": b"\xca\xfe"})
        self.assertIsNone(source_of(zf, "Tree"))

    def test_none_for_a_class_only_archive(self):
        zf = self.open({"t/Tree.class": b"\xca\xfe\xba\xbe"})
        self.assertIsNone(source_of(zf, "Tree"))

    def test_looks_inside_a_wrapped_jar(self):
        inner = zip_bytes({"t/Tree.java": "class Tree { /*inner*/ }"})
        zf = self.open({"readme.txt": "hi", "export/project.jar": inner})
        self.assertEqual(source_of(zf, "Tree"), "class Tree { /*inner*/ }")

    def test_direct_copy_wins_over_a_nested_one(self):
        inner = zip_bytes({"Tree.java": "class Tree { /*inner*/ }"})
        zf = self.open({"Tree.java": "class Tree { /*outer*/ }", "p.jar": inner})
        self.assertIn("outer", source_of(zf, "Tree"))

    def test_only_one_level_deep(self):
        deepest = zip_bytes({"Tree.java": "class Tree { }"})
        middle = zip_bytes({"b.jar": deepest})
        zf = self.open({"a.zip": middle})
        self.assertIsNone(source_of(zf, "Tree"))

    def test_corrupt_nested_archive_is_skipped(self):
        zf = self.open({"bad.jar": b"not a zip", "good.jar": zip_bytes({"Tree.java": "ok"})})
        self.assertEqual(source_of(zf, "Tree"), "ok")

    def test_two_copies_resolve_alphabetically_first_like_grade_py(self):
        zf = self.open({"v2/Tree.java": "second", "v1/Tree.java": "first"})
        self.assertEqual(source_of(zf, "Tree"), "first")

    def test_windows_separators_and_bom(self):
        zf = self.open({"src\\t\\Tree.java": b"\xef\xbb\xbfclass Tree {}"})
        self.assertEqual(source_of(zf, "Tree"), "class Tree {}")


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.starter = self.root / "starter"
        self.subs = self.root / "subs"
        self.starter.mkdir()
        self.subs.mkdir()
        (self.starter / "Tree.java").write_text(STARTER, encoding="utf-8")

    def jar(self, sid, files, ext=".jar"):
        make_zip(self.subs / (sid + ext), files)

    def report(self, allow=None, **kw):
        kw.setdefault("classes", None)
        kw.setdefault("show_diff", False)
        kw.setdefault("diff_lines", 20)
        return build_report(self.subs, self.starter,
                            ALLOW if allow is None else allow, **kw)


class TestBuildReport(ArchiveCase):
    def test_untouched_jar_is_counted_and_not_listed(self):
        self.jar("10000001", {"src/t/Tree.java": STARTER})
        out = self.report()
        self.assertIn("untouched: 1", out)
        self.assertNotIn("10000001", out)

    def test_reformatted_jar_is_still_untouched(self):
        text = STARTER.replace("return size == 0;", "return size==0 ;").replace("    ", "\t")
        self.jar("10000001", {"Tree.java": text})
        self.assertIn("untouched: 1", self.report())

    def test_changed_find_is_listed_under_the_student_id(self):
        self.jar("10000001", {"Tree.java": STARTER})
        self.jar("10000002", {"src/t/Tree.java": edit("return temp;", "return null;")})
        out = self.report()
        self.assertIn("untouched: 1", out)
        block = out.split("10000002", 1)[1]
        self.assertIn("Tree", block)
        self.assertIn("CHANGED find(int)", block)
        self.assertNotIn("10000001", out)

    def test_deleted_main_and_added_field_lines(self):
        student = edit(MAIN_BLOCK, "")
        student = edit("public int size;", "public int size;\n    int cache;", student)
        self.jar("10000003", {"Tree.java": student})
        out = self.report()
        self.assertIn("MISSING main(String[])", out)
        self.assertIn("FIELDS +int cache;", out)

    def test_class_only_jar_is_no_source(self):
        self.jar("10000004", {"t/Tree.class": b"\xca\xfe\xba\xbe"})
        out = self.report()
        self.assertIn("10000004", out)
        self.assertIn("NO SOURCE (no Tree.java in the archive)", out)
        self.assertIn("untouched: 0", out)

    def test_zip_wrapping_a_jar_is_audited(self):
        inner = zip_bytes({"t/Tree.java": edit("this.size = size;", "this.size = 5;")})
        self.jar("10000005", {"export/project.jar": inner, "notes.txt": "hi"}, ext=".zip")
        out = self.report()
        self.assertIn("10000005", out)
        self.assertIn("CHANGED Tree(Node,int)", out)

    def test_another_questions_file_is_one_whole_file_line(self):
        other = "package q; public class Tree { void a() {} void b() {} void c() {} }"
        self.jar("10000006", {"Tree.java": other})
        out = self.report()
        block = out.split("10000006", 1)[1]
        self.assertEqual(block.count("WHOLE FILE DIFFERS"), 1)
        self.assertNotIn("CHANGED", block)
        self.assertNotIn("MISSING", block)

    def test_students_are_listed_in_id_order(self):
        for sid in ("10000030", "10000010", "10000020"):
            self.jar(sid, {"Tree.java": edit("return temp;", "return null;")})
        out = self.report()
        at = [out.index(s) for s in ("10000010", "10000020", "10000030")]
        self.assertEqual(at, sorted(at))

    def test_unreadable_archive_is_reported_not_raised(self):
        (self.subs / "10000009.jar").write_bytes(b"this is not a zip")
        self.jar("10000008", {"Tree.java": edit("return temp;", "return null;")})
        out = self.report()
        self.assertIn("UNREADABLE ARCHIVE", out.split("10000009", 1)[1])
        self.assertIn("CHANGED find(int)", out)

    def test_non_archive_files_in_submissions_are_ignored(self):
        (self.subs / "notes.txt").write_text("x")
        (self.subs / "10000001.java").write_text(STARTER)
        self.assertIn("untouched: 0", self.report())

    def test_header_names_starter_allowed_and_counts(self):
        self.jar("10000001", {"Tree.java": STARTER})
        out = self.report()
        self.assertIn(str(self.starter), out)
        self.assertIn("height, rebuild", out)
        self.assertIn("4 given", out)
        self.assertIn("height()", out)       # the exempted members are named

    def test_allowed_name_that_matches_nothing_is_called_out(self):
        self.jar("10000001", {"Tree.java": STARTER})
        out = self.report(allow={"height", "heigth"})
        self.assertIn("not found", out)
        self.assertIn("heigth", out.split("not found", 1)[1])

    def test_classes_default_to_every_java_in_the_starter(self):
        (self.starter / "Node.java").write_text("class Node { int data; }")
        self.jar("10000001", {"Tree.java": STARTER})
        out = self.report()
        self.assertIn("NO SOURCE (no Node.java in the archive)", out)
        only_tree = self.report(classes=["Tree"])
        self.assertNotIn("Node.java", only_tree)
        self.assertIn("untouched: 1", only_tree)

    def test_report_is_read_only(self):
        self.jar("10000001", {"Tree.java": STARTER})
        self.jar("10000002", {"Tree.java": edit("return temp;", "return null;")})
        before = tree_snapshot(self.root)
        self.report(show_diff=True)
        self.assertEqual(tree_snapshot(self.root), before)


class TestDiff(ArchiveCase):
    def changed_find(self):
        # every line of find(int) edited
        return edit('''        Node temp = root;
        while (temp != null && temp.data != v) { temp = v < temp.data ? temp.left : temp.right; }
        return temp;
''', '''        Node cur = root;
        while (cur != null) {
            if (cur.data == v) return cur;
            cur = v < cur.data ? cur.left : cur.right;
        }
        return null;
''')

    def test_no_diff_unless_asked(self):
        self.jar("10000002", {"Tree.java": self.changed_find()})
        out = self.report()
        self.assertFalse(any(l.startswith(("-", "+")) for l in stripped_lines(out)))

    def test_diff_is_a_unified_diff_starter_first_under_its_changed_line(self):
        self.jar("10000002", {"Tree.java": self.changed_find()})
        lines = stripped_lines(self.report(show_diff=True))
        head = next(i for i, l in enumerate(lines) if l.endswith("CHANGED find(int)"))
        old = lines.index("-Node temp = root;")
        new = lines.index("+Node cur = root;")
        self.assertLess(head, old)
        self.assertLess(old, new)

    def test_diff_is_capped_at_diff_lines(self):
        self.jar("10000002", {"Tree.java": self.changed_find()})
        lines = stripped_lines(self.report(show_diff=True, diff_lines=3))
        head = next(i for i, l in enumerate(lines) if l.endswith("CHANGED find(int)"))
        more = next(i for i, l in enumerate(lines) if "more diff line" in l)
        self.assertEqual(more - head - 1, 3)      # exactly 3 diff lines, then the note

    def test_diff_under_the_cap_has_no_truncation_note(self):
        self.jar("10000002", {"Tree.java": self.changed_find()})
        self.assertNotIn("more diff line", self.report(show_diff=True, diff_lines=500))

    def test_diff_ignores_whitespace_only_differences(self):
        student = self.changed_find().replace("Node cur = root;", "Node   cur   =\t  root;")
        self.jar("10000002", {"Tree.java": student})
        self.assertIn("+Node cur = root;", stripped_lines(self.report(show_diff=True)))

    def test_diff_shows_real_string_contents_not_blanks(self):
        student = edit("Tree t = new Tree(null, 0);",
                       'Tree t = new Tree(null, 1); log("cheat");')
        self.jar("10000002", {"Tree.java": student})
        self.assertIn('+Tree t = new Tree(null, 1); log("cheat");',
                      stripped_lines(self.report(show_diff=True)))

    def test_missing_member_gets_no_diff(self):
        self.jar("10000002", {"Tree.java": edit(MAIN_BLOCK, "")})
        out = self.report(show_diff=True)
        self.assertIn("MISSING main(String[])", out)
        self.assertFalse(any(l.startswith(("-", "+")) for l in stripped_lines(out)))


class TestMainAndErrors(ArchiveCase):
    def run_main(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(list(argv))
        return code, buf.getvalue()

    def args(self, *extra):
        return ["--starter", str(self.starter), "--submissions", str(self.subs),
                "--allow", "height,rebuild", *extra]

    def test_missing_starter_dir_exits_with_a_clear_message(self):
        with self.assertRaises(SystemExit) as cm:
            build_report(self.subs, self.root / "nope", ALLOW, None, False, 20)
        self.assertIn("starter", str(cm.exception.code).lower())
        self.assertIn("nope", str(cm.exception.code))

    def test_starter_without_java_files_exits_with_a_clear_message(self):
        (self.starter / "Tree.java").unlink()
        (self.starter / "readme.txt").write_text("x")
        with self.assertRaises(SystemExit) as cm:
            build_report(self.subs, self.starter, ALLOW, None, False, 20)
        self.assertIn(".java", str(cm.exception.code))

    def test_missing_submissions_dir_exits_with_a_clear_message(self):
        with self.assertRaises(SystemExit) as cm:
            build_report(self.root / "gone", self.starter, ALLOW, None, False, 20)
        self.assertIn("gone", str(cm.exception.code))

    def test_class_not_in_starter_exits_with_a_clear_message(self):
        with self.assertRaises(SystemExit) as cm:
            build_report(self.subs, self.starter, ALLOW, ["Treee"], False, 20)
        self.assertIn("Treee", str(cm.exception.code))

    def test_main_prints_the_report_and_returns_zero(self):
        self.jar("10000002", {"Tree.java": edit("return temp;", "return null;")})
        code, out = self.run_main(*self.args())
        self.assertEqual(code, 0)
        self.assertIn("CHANGED find(int)", out)

    def test_main_flags_are_wired(self):
        self.jar("10000002", {"Tree.java": edit("return temp;", "return null;")})
        code, out = self.run_main(*self.args("--classes", "Tree", "--diff", "--diff-lines", "2"))
        self.assertEqual(code, 0)
        self.assertIn("-return temp;", stripped_lines(out))

    def test_allow_is_required_and_may_be_empty(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--starter", str(self.starter), "--submissions", str(self.subs)])
        self.jar("10000001", {"Tree.java": STARTER})
        code, out = self.run_main("--starter", str(self.starter),
                                  "--submissions", str(self.subs), "--allow", "")
        self.assertEqual(code, 0)
        self.assertIn("untouched: 1", out)

    def test_bad_diff_lines_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(self.args("--diff-lines", "0"))

    def test_subprocess_exit_code_zero_and_no_files_written(self):
        self.jar("10000002", {"Tree.java": edit("return temp;", "return null;")})
        before = tree_snapshot(self.root)
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), *self.args("--diff")],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CHANGED find(int)", proc.stdout)
        self.assertEqual(tree_snapshot(self.root), before)

    def test_non_ascii_student_text_cannot_crash_a_redirected_run(self):
        # Thai text in a string literal on a changed line, stdout forced to ASCII
        student = edit("Tree t = new Tree(null, 0);",
                       'Tree t = new Tree(null, 1); say("%s");' % THAI)
        self.jar("10000002", {"Tree.java": student})
        env = dict(os.environ, PYTHONIOENCODING="ascii")
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), *self.args("--diff")],
                              capture_output=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(b"CHANGED main(String[])", proc.stdout)

    def test_subprocess_missing_starter_exits_nonzero_with_text_on_stderr(self):
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "--starter",
                               str(self.root / "nope"), "--submissions", str(self.subs),
                               "--allow", "x"], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("nope", proc.stderr)


class TestFollowUpReport(ArchiveCase):
    """The three polish items as the TA sees them, through build_report."""

    def setUp(self):
        super().setUp()
        (self.starter / "Tree.java").unlink()
        (self.starter / "Q.java").write_text(QSTARTER, encoding="utf-8")

    def test_fully_qualified_student_is_untouched(self):
        self.jar("10000001", {"src/bst/Q.java": qualify(QSTARTER)})
        out = self.report(allow=set())
        self.assertIn("untouched: 1", out)
        self.assertNotIn("10000001", out)

    def test_qualified_student_with_one_real_edit_lists_only_that_member(self):
        student = qualify(QSTARTER).replace("return v == 0;", "return v != 0;")
        self.jar("10000002", {"Q.java": student})
        out = self.report(allow=set(), show_diff=True)
        self.assertEqual([l for l in stripped_lines(out) if l.startswith("Q  ")
                          and "given member" not in l],
                         ["Q  CHANGED find(int)"])
        diff = [l for l in stripped_lines(out) if l.startswith(("-", "+"))]
        self.assertEqual(diff, ["-public boolean find(int v) { return v == 0; }",
                                "+public boolean find(int v) { return v != 0; }"])
        self.assertNotIn("java.util.", out)

    def test_diff_of_a_qualified_member_has_no_qualifier_noise(self):
        student = qualify(QSTARTER).replace(
            "return i.next(); }", "return i.next() + 1; }")
        self.jar("10000003", {"Q.java": student})
        out = self.report(allow=set(), show_diff=True)
        body = "\n".join(l for l in stripped_lines(out) if l.startswith(("-", "+", " ")))
        self.assertNotIn("iterator.", body)
        self.assertIn("-public int findMin() { Iterator<Integer> i = new TreeIterator<>(); return i.next(); }",
                      stripped_lines(out))

    def test_class_with_two_given_members_lists_both_changes(self):
        (self.starter / "Small.java").write_text(
            "class Small { int a() { return 1; } int b() { return 2; } }", encoding="utf-8")
        self.jar("10000004", {
            "Q.java": QSTARTER,
            "Small.java": "class Small { int a() { return 9; } int b() { return 8; } }"})
        out = self.report(allow=set())
        self.assertNotIn("WHOLE FILE DIFFERS", out)
        self.assertIn("Small  CHANGED a()", out)
        self.assertIn("Small  CHANGED b()", out)

    def test_fields_are_displayed_as_written_in_the_report(self):
        student = QSTARTER.replace(
            "private Iterator<Integer> it;",
            "private Iterator<Integer> it;\n    ArrayList<Integer>   numbers\n        = new ArrayList<>();\n"
            "    private int index;\n    int[] a = new int[size];")
        self.jar("10000005", {"Q.java": student})
        out = self.report(allow=set())
        self.assertIn("FIELDS +ArrayList<Integer> numbers = new ArrayList<>();", out)
        self.assertIn("FIELDS +private int index;", out)
        self.assertIn("FIELDS +int[] a = new int[size];", out)


if __name__ == "__main__":
    unittest.main()
