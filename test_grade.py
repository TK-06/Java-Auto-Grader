import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from grade import (
    RMTREE_RETRY_ATTEMPTS,
    CompileResult,
    bare_student_id,
    check_output_writable,
    check_structure_baseline,
    collect_required_class_names,
    collect_test_results,
    compile_submission_with_fallback,
    discover_submissions,
    filter_unusable_fallback_matches,
    find_class_fallback_files,
    find_class_files,
    find_java_files,
    find_junit_jar,
    find_nested_archives,
    grade_student,
    infer_unnamed_package_classes,
    load_structure_baseline,
    prepare_build_dir,
    resolve_class_fallback_dest,
    rewrite_imports_of_renamed_packages,
    rmtree_with_retry,
    strip_imports_of_packages,
    strip_package_declaration,
    write_csv,
    write_scores_csv,
)


class TestStripImportsOfPackages(unittest.TestCase):
    def test_removes_import_of_a_flattened_package(self):
        text = "import main.java.Bot;\nimport java.util.Scanner;\n\npublic class TestBot {\n}\n"

        new_text = strip_imports_of_packages(text, {"main.java"})

        self.assertNotIn("import main.java.Bot;", new_text)
        self.assertIn("import java.util.Scanner;", new_text)

    def test_leaves_text_unchanged_when_no_flattened_packages(self):
        text = "import main.java.Bot;\n\npublic class TestBot {\n}\n"

        new_text = strip_imports_of_packages(text, set())

        self.assertEqual(new_text, text)


class TestRewriteImportsOfRenamedPackages(unittest.TestCase):
    def test_rewrites_import_of_a_renamed_package(self):
        text = "import Q1_toStudent.logic.Station;\nimport java.util.List;\n\npublic class CPTSMachine {\n}\n"

        new_text = rewrite_imports_of_renamed_packages(text, {"Q1_toStudent.logic": "logic"})

        self.assertIn("import logic.Station;", new_text)
        self.assertNotIn("Q1_toStudent", new_text)
        self.assertIn("import java.util.List;", new_text)

    def test_leaves_text_unchanged_when_no_renamed_packages(self):
        text = "import Q1_toStudent.logic.Station;\n\npublic class CPTSMachine {\n}\n"

        new_text = rewrite_imports_of_renamed_packages(text, {})

        self.assertEqual(new_text, text)


class TestPrepareBuildDirStripsPackage(unittest.TestCase):
    def test_strips_package_declaration_from_copied_student_file_and_notes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            student_file = src_dir / "Bot.java"
            student_file.write_text(
                "package main.java;\n\npublic class Bot {\n}\n", encoding="utf-8"
            )
            build_root = tmp_path / "build"

            build_dir, notes = prepare_build_dir(
                "1", [student_file], [], build_root
            )

            copied_text = (build_dir / "Bot.java").read_text(encoding="utf-8")
            self.assertNotIn("package", copied_text)
            self.assertTrue(
                any("stripped package declaration" in note for note in notes),
                notes,
            )


    def test_does_not_strip_package_when_official_test_imports_from_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            student_file = src_dir / "CPTSMachine.java"
            student_file.write_text(
                "package application;\n\npublic class CPTSMachine {\n}\n", encoding="utf-8"
            )
            tests_dir = tmp_path / "tests"
            tests_dir.mkdir()
            official_test = tests_dir / "TestCPTSMachine2.java"
            official_test.write_text(
                "import application.CPTSMachine;\n\npublic class TestCPTSMachine2 {\n}\n",
                encoding="utf-8",
            )
            build_root = tmp_path / "build"

            build_dir, notes = prepare_build_dir(
                "1", [student_file], [official_test], build_root
            )

            copied_text = (build_dir / "CPTSMachine.java").read_text(encoding="utf-8")
            self.assertIn("package application;", copied_text)
            self.assertFalse(
                any("stripped package declaration" in note for note in notes), notes
            )

    def test_rewrites_sibling_files_import_of_the_same_renamed_package(self):
        """The other half of the same real-world bug: CPTSMachine.java
        imports Station via the student's own (prefixed) package name, not
        the canonical one. Renaming Station's declaration alone isn't
        enough - CPTSMachine's import of the now-gone prefixed name must be
        rewritten too, or it fails with 'package ... does not exist'."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            station_file = src_dir / "Station.java"
            station_file.write_text(
                "package Q1_toStudent.logic;\n\npublic class Station {\n}\n", encoding="utf-8"
            )
            machine_file = src_dir / "CPTSMachine.java"
            machine_file.write_text(
                "package Q1_toStudent.application;\n\n"
                "import Q1_toStudent.logic.Station;\n\n"
                "public class CPTSMachine {\n    private Station s;\n}\n",
                encoding="utf-8",
            )
            tests_dir = tmp_path / "tests"
            tests_dir.mkdir()
            official_test = tests_dir / "TestCPTSMachine2.java"
            official_test.write_text(
                "import application.CPTSMachine;\nimport logic.Station;\n\n"
                "public class TestCPTSMachine2 {\n}\n",
                encoding="utf-8",
            )
            build_root = tmp_path / "build"

            build_dir, _notes = prepare_build_dir(
                "1", [station_file, machine_file], [official_test], build_root
            )

            machine_text = (build_dir / "CPTSMachine.java").read_text(encoding="utf-8")
            self.assertIn("import logic.Station;", machine_text)
            self.assertNotIn("Q1_toStudent", machine_text)

    def test_rewrites_package_nested_under_an_extra_prefix_instead_of_stripping(self):
        """Regression test for the bug behind 5 false-zero scores in the
        2026-08-10 grading run: an IDE inferring `Q1_toStudent.application`
        from a source-root folder named after the assignment must still end
        up compiled as exactly `application`, not flattened to the unnamed
        package - otherwise the official test's own `import application.X;`
        can't resolve it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            student_file = src_dir / "CPTSMachine.java"
            student_file.write_text(
                "package Q1_toStudent.application;\n\npublic class CPTSMachine {\n}\n",
                encoding="utf-8",
            )
            tests_dir = tmp_path / "tests"
            tests_dir.mkdir()
            official_test = tests_dir / "TestCPTSMachine2.java"
            official_test.write_text(
                "import application.CPTSMachine;\n\npublic class TestCPTSMachine2 {\n}\n",
                encoding="utf-8",
            )
            build_root = tmp_path / "build"

            build_dir, notes = prepare_build_dir(
                "1", [student_file], [official_test], build_root
            )

            copied_text = (build_dir / "CPTSMachine.java").read_text(encoding="utf-8")
            self.assertIn("package application;", copied_text)
            self.assertNotIn("Q1_toStudent", copied_text)
            self.assertTrue(
                any("rewrote package declaration" in note for note in notes), notes
            )
            self.assertFalse(
                any("stripped package declaration" in note for note in notes), notes
            )

    def test_strips_import_of_flattened_package_from_a_sibling_student_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            bot_file = src_dir / "Bot.java"
            bot_file.write_text(
                "package main.java;\n\npublic class Bot {\n}\n", encoding="utf-8"
            )
            test_bot_file = src_dir / "TestBot.java"
            test_bot_file.write_text(
                "package test.java;\n\nimport main.java.Bot;\n\npublic class TestBot {\n}\n",
                encoding="utf-8",
            )
            build_root = tmp_path / "build"

            build_dir, _notes = prepare_build_dir(
                "1", [bot_file, test_bot_file], [], build_root
            )

            copied_text = (build_dir / "TestBot.java").read_text(encoding="utf-8")
            self.assertNotIn("import main.java.Bot;", copied_text)


class TestStripPackageDeclaration(unittest.TestCase):
    def test_strips_leading_package_declaration(self):
        text = "package main.java;\n\npublic class Bot {\n}\n"

        new_text, declared_package, rewritten_to = strip_package_declaration(text)

        self.assertEqual(declared_package, "main.java")
        self.assertIsNone(rewritten_to)
        self.assertNotIn("package", new_text)
        self.assertIn("public class Bot {", new_text)

    def test_leaves_text_unchanged_when_no_package_declaration(self):
        text = "public class Bot {\n}\n"

        new_text, declared_package, rewritten_to = strip_package_declaration(text)

        self.assertIsNone(declared_package)
        self.assertIsNone(rewritten_to)
        self.assertEqual(new_text, text)

    def test_leaves_package_declaration_when_in_keep_set(self):
        text = "package application;\n\npublic class CPTSMachine {\n}\n"

        new_text, declared_package, rewritten_to = strip_package_declaration(
            text, keep_packages={"application"}
        )

        self.assertIsNone(declared_package)
        self.assertIsNone(rewritten_to)
        self.assertEqual(new_text, text)

    def test_rewrites_package_nested_under_extra_prefix_to_the_required_name(self):
        text = "package Q1_toStudent.application;\n\npublic class CPTSMachine {\n}\n"

        new_text, declared_package, rewritten_to = strip_package_declaration(
            text, keep_packages={"application", "logic", "test.grader"}
        )

        self.assertEqual(declared_package, "Q1_toStudent.application")
        self.assertEqual(rewritten_to, "application")
        self.assertIn("package application;", new_text)
        self.assertNotIn("Q1_toStudent", new_text)

    def test_rewrites_using_the_longest_matching_required_package(self):
        # `test.grader` and `grader` are both plausible keep-packages here;
        # the more specific (longer) one must win so the class doesn't end
        # up in the wrong package.
        text = "package Q1_toStudent.test.grader;\n\npublic class TestCPTSMachine {\n}\n"

        new_text, declared_package, rewritten_to = strip_package_declaration(
            text, keep_packages={"grader", "test.grader"}
        )

        self.assertEqual(rewritten_to, "test.grader")
        self.assertIn("package test.grader;", new_text)

    def test_strips_to_unnamed_when_no_required_package_matches_even_as_a_suffix(self):
        text = "package main.java;\n\npublic class Bot {\n}\n"

        new_text, declared_package, rewritten_to = strip_package_declaration(
            text, keep_packages={"application", "logic"}
        )

        self.assertEqual(declared_package, "main.java")
        self.assertIsNone(rewritten_to)
        self.assertNotIn("package", new_text)


class TestCollectTestResults(unittest.TestCase):
    def test_extracts_the_assertion_message_for_a_failed_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp)
            (reports_dir / "TEST-junit-jupiter.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<testsuite name="JUnit Jupiter" tests="2" skipped="0" failures="1" errors="0">\n'
                '<testcase name="testGetDescription()" classname="test.grader.TestTicket2">\n'
                '<failure message="expected: &lt;A&gt; but was: &lt;B&gt;" '
                'type="org.opentest4j.AssertionFailedError">org.opentest4j.AssertionFailedError: '
                "expected: &lt;A&gt; but was: &lt;B&gt;\n\tat some.Stack(Trace.java:1)\n"
                "</failure>\n"
                "</testcase>\n"
                '<testcase name="testSetStation()" classname="test.grader.TestTicket2">\n'
                "</testcase>\n"
                "</testsuite>\n",
                encoding="utf-8",
            )

            results = collect_test_results(reports_dir)

            failed = next(tc for tc in results if tc.method == "testGetDescription")
            passed = next(tc for tc in results if tc.method == "testSetStation")
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.detail, "expected: <A> but was: <B>")
            self.assertEqual(passed.status, "passed")
            self.assertEqual(passed.detail, "")

    def test_falls_back_to_stack_trace_first_line_when_no_message_attribute(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp)
            (reports_dir / "TEST-junit-jupiter.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<testsuite name="JUnit Jupiter" tests="1" skipped="0" failures="0" errors="1">\n'
                '<testcase name="testAddStation()" classname="test.grader.TestCPTSMachine2">\n'
                '<error type="java.lang.NullPointerException">java.lang.NullPointerException'
                "\n\tat some.Stack(Trace.java:1)\n</error>\n"
                "</testcase>\n"
                "</testsuite>\n",
                encoding="utf-8",
            )

            results = collect_test_results(reports_dir)

            self.assertEqual(results[0].status, "failed")
            self.assertEqual(results[0].detail, "java.lang.NullPointerException")


class TestCompileSubmissionWithFallback(unittest.TestCase):
    def test_excludes_a_broken_leftover_test_file_and_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            (build_dir / "CPTSMachine.java").write_text("public class CPTSMachine {}\n", encoding="utf-8")
            (build_dir / "TestCPTSMachine.java").write_text(
                "import org.junit.jupiter.api.Test;\npublic class TestCPTSMachine { broken!! }\n",
                encoding="utf-8",
            )

            with mock.patch("grade.compile_submission") as mock_compile:
                mock_compile.side_effect = [
                    CompileResult(False, build_dir / "classes", "TestCPTSMachine.java:2: error: illegal start"),
                    CompileResult(True, build_dir / "classes"),
                ]
                result, notes = compile_submission_with_fallback(
                    build_dir, Path("junit.jar"), 30, official_names=set()
                )

            self.assertTrue(result.success)
            self.assertEqual(mock_compile.call_count, 2)
            self.assertTrue(
                any("excluded student's leftover test file(s) TestCPTSMachine.java" in n for n in notes), notes
            )
            self.assertTrue(any("illegal start" in n for n in notes), notes)
            self.assertFalse((build_dir / "TestCPTSMachine.java").exists())
            self.assertTrue((build_dir / "_excluded_extra" / "TestCPTSMachine.java").exists())

    def test_restores_files_and_keeps_original_error_when_exclusion_does_not_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            (build_dir / "CPTSMachine.java").write_text("public class CPTSMachine { broken!! }\n", encoding="utf-8")
            (build_dir / "TestCPTSMachine.java").write_text(
                "import org.junit.jupiter.api.Test;\npublic class TestCPTSMachine {}\n", encoding="utf-8"
            )

            with mock.patch("grade.compile_submission") as mock_compile:
                mock_compile.side_effect = [
                    CompileResult(False, build_dir / "classes", "CPTSMachine.java:1: error: illegal start"),
                    CompileResult(False, build_dir / "classes", "CPTSMachine.java:1: error: illegal start"),
                ]
                result, notes = compile_submission_with_fallback(
                    build_dir, Path("junit.jar"), 30, official_names=set()
                )

            self.assertFalse(result.success)
            self.assertEqual(result.output, "CPTSMachine.java:1: error: illegal start")
            self.assertEqual(notes, [])
            self.assertTrue((build_dir / "TestCPTSMachine.java").exists())

    def test_no_retry_when_nothing_looks_like_a_leftover_test_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            (build_dir / "CPTSMachine.java").write_text("public class CPTSMachine { broken!! }\n", encoding="utf-8")

            with mock.patch("grade.compile_submission") as mock_compile:
                mock_compile.return_value = CompileResult(False, build_dir / "classes", "some error")
                result, notes = compile_submission_with_fallback(
                    build_dir, Path("junit.jar"), 30, official_names=set()
                )

            self.assertFalse(result.success)
            self.assertEqual(notes, [])
            mock_compile.assert_called_once()


class TestLoadStructureBaseline(unittest.TestCase):
    def test_returns_none_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_structure_baseline(Path(tmp)))

    def test_parses_a_valid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tests_dir = Path(tmp)
            (tests_dir / "structure.json").write_text(
                json.dumps({"required_classes": ["Station", "Ticket", "CPTSMachine"]}),
                encoding="utf-8",
            )

            result = load_structure_baseline(tests_dir)

            self.assertEqual(result, ["Station", "Ticket", "CPTSMachine"])

    def test_exits_loudly_on_a_malformed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tests_dir = Path(tmp)
            (tests_dir / "structure.json").write_text(
                json.dumps({"required_classes": "Station"}), encoding="utf-8"
            )

            with self.assertRaises(SystemExit):
                load_structure_baseline(tests_dir)


class TestCheckStructureBaseline(unittest.TestCase):
    def test_no_violations_when_everything_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            (build_dir / "Station.java").write_text("public class Station {}\n", encoding="utf-8")
            (build_dir / "Ticket.java").write_text("public class Ticket {}\n", encoding="utf-8")
            (build_dir / "TestStation2.java").write_text("public class TestStation2 {}\n", encoding="utf-8")

            violations = check_structure_baseline(
                build_dir, official_names={"TestStation2.java"}, required_classes=["Station", "Ticket"]
            )

            self.assertEqual(violations, [])

    def test_reports_a_missing_required_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            (build_dir / "Station.java").write_text("public class Station {}\n", encoding="utf-8")

            violations = check_structure_baseline(
                build_dir, official_names=set(), required_classes=["Station", "Ticket"]
            )

            self.assertEqual(len(violations), 1)
            self.assertIn("missing required class Ticket", violations[0])

    def test_extra_classes_are_never_flagged(self):
        # Mental model: if we swapped in the official test file on the
        # student's own machine, would it still work? An unused extra
        # class - or a leftover test file from a prior week - doesn't
        # break that, so neither is a violation.
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            (build_dir / "Station.java").write_text("public class Station {}\n", encoding="utf-8")
            (build_dir / "Helper.java").write_text("public class Helper {}\n", encoding="utf-8")
            (build_dir / "TestStation.java").write_text(
                "import org.junit.jupiter.api.Test;\npublic class TestStation {}\n", encoding="utf-8"
            )

            violations = check_structure_baseline(
                build_dir, official_names=set(), required_classes=["Station"]
            )

            self.assertEqual(violations, [])

    def test_reports_multiple_missing_classes_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            (build_dir / "Helper.java").write_text("public class Helper {}\n", encoding="utf-8")

            violations = check_structure_baseline(
                build_dir, official_names=set(), required_classes=["Station", "Ticket"]
            )

            self.assertEqual(len(violations), 2)


class TestGradeStudentPreservesFailedBuilds(unittest.TestCase):
    def _junit_jar(self) -> Path:
        return find_junit_jar(Path(__file__).resolve().parent / "lib")

    def test_archives_the_build_dir_when_compilation_fails(self):
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            bad_file = src_dir / "Broken.java"
            bad_file.write_text("public class Broken { this is not java }\n", encoding="utf-8")
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "12345678", "1", [bad_file], [], [], [], junit_jar, build_root,
                30, False, None, None, failed_build_root,
            )

            self.assertEqual(row["compiled"], "no")
            audit_dir = failed_build_root / "12345678__1"
            self.assertTrue(audit_dir.exists())
            self.assertTrue((audit_dir / "Broken.java").exists())
            self.assertFalse((build_root / "1").exists())  # build_tmp copy still cleaned up as normal

    def test_does_not_archive_a_successful_compile(self):
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            good_file = src_dir / "Fine.java"
            good_file.write_text("public class Fine {}\n", encoding="utf-8")
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "87654321", "1", [good_file], [], [], [], junit_jar, build_root,
                30, False, None, None, failed_build_root,
            )

            self.assertEqual(row["compiled"], "yes")
            self.assertEqual(list(failed_build_root.iterdir()), [])


class TestGradeStudentStructureBaseline(unittest.TestCase):
    def test_missing_required_class_is_rejected_before_compiling(self):
        # junit_jar is never touched on this path - the structure check
        # returns before compile_submission_with_fallback is ever called.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            station_file = src_dir / "Station.java"
            station_file.write_text("public class Station {}\n", encoding="utf-8")
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "11112222", "1", [station_file], [], [], [], Path("unused.jar"), build_root,
                30, False, None, ["Station", "Ticket"], failed_build_root,
            )

            self.assertEqual(row["compiled"], "no")
            self.assertIn("STRUCTURE ERROR", row["notes"])
            self.assertIn("missing required class Ticket", row["notes"])
            audit_dir = failed_build_root / "11112222__1"
            self.assertTrue(audit_dir.exists())
            self.assertTrue((audit_dir / "Station.java").exists())

    def test_matching_structure_proceeds_to_compile(self):
        junit_jar = find_junit_jar(Path(__file__).resolve().parent / "lib")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            station_file = src_dir / "Station.java"
            station_file.write_text("public class Station {}\n", encoding="utf-8")
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "33334444", "1", [station_file], [], [], [], junit_jar, build_root,
                30, False, None, ["Station"], failed_build_root,
            )

            self.assertEqual(row["compiled"], "yes")
            self.assertEqual(list(failed_build_root.iterdir()), [])


class TestFindJavaFiles(unittest.TestCase):
    def test_skips_known_build_and_ide_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "Station.java").write_text("public class Station {}\n", encoding="utf-8")
            for skip_dir in ("out", "target", "bin", "build", ".git", ".idea", ".vscode", ".settings"):
                d = root / skip_dir
                d.mkdir()
                (d / "Leftover.java").write_text("public class Leftover {}\n", encoding="utf-8")

            found = find_java_files(root)

            self.assertEqual({f.name for f in found}, {"Station.java"})

    def test_walks_normally_named_nested_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "src" / "logic"
            nested.mkdir(parents=True)
            (nested / "Station.java").write_text("public class Station {}\n", encoding="utf-8")

            found = find_java_files(root)

            self.assertEqual([f.name for f in found], ["Station.java"])


class TestRmtreeWithRetry(unittest.TestCase):
    def test_removes_a_normal_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "victim"
            target.mkdir()
            (target / "file.txt").write_text("x", encoding="utf-8")

            rmtree_with_retry(target)

            self.assertFalse(target.exists())

    def test_retries_past_a_transient_lock_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "victim"
            target.mkdir()

            real_rmtree = shutil.rmtree
            attempts = {"n": 0}

            def flaky_rmtree(path, *args, **kwargs):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    raise PermissionError("simulated transient lock")
                real_rmtree(path, *args, **kwargs)

            with mock.patch("grade.shutil.rmtree", side_effect=flaky_rmtree), \
                 mock.patch("grade.time.sleep") as mock_sleep:
                rmtree_with_retry(target)

            self.assertEqual(attempts["n"], 3)
            self.assertEqual(mock_sleep.call_count, 2)
            self.assertFalse(target.exists())

    def test_exits_loudly_when_the_lock_never_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "victim"
            target.mkdir()

            with mock.patch("grade.shutil.rmtree", side_effect=PermissionError("stuck")), \
                 mock.patch("grade.time.sleep") as mock_sleep:
                with self.assertRaises(SystemExit):
                    rmtree_with_retry(target)

            self.assertEqual(mock_sleep.call_count, RMTREE_RETRY_ATTEMPTS - 1)


class TestBareStudentId(unittest.TestCase):
    def test_strips_a_week_question_tag(self):
        self.assertEqual(bare_student_id("6638002421_w1_q1"), "6638002421")

    def test_leaves_a_plain_numeric_id_unchanged(self):
        self.assertEqual(bare_student_id("6638002421"), "6638002421")

    def test_falls_back_to_the_original_when_it_does_not_start_with_digits(self):
        self.assertEqual(bare_student_id("some_weird_name"), "some_weird_name")


class TestWriteScoresCsv(unittest.TestCase):
    def test_writes_bare_ids_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "mcvScore.csv"
            rows = [
                {"student_id": "6638002421_w1_q1", "score": 14.0},
                {"student_id": "6638030021_w1_q1", "score": 0},
            ]

            write_scores_csv(rows, out_path)

            content = out_path.read_text(encoding="utf-8")
            self.assertIn("6638002421,14.0", content)
            self.assertIn("6638030021,0", content)
            self.assertNotIn("_w1_q1", content)

    def test_has_no_header_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "mcvScore.csv"
            rows = [{"student_id": "6638002421_w1_q1", "score": 14.0}]

            write_scores_csv(rows, out_path)

            lines = out_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "6638002421,14.0")
            self.assertNotIn("student_id", lines[0])


class TestFindNestedArchives(unittest.TestCase):
    def test_finds_zip_and_jar_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inner.jar").write_bytes(b"")
            (root / "sub").mkdir()
            (root / "sub" / "inner2.zip").write_bytes(b"")
            (root / "Notes.txt").write_text("x", encoding="utf-8")

            found = find_nested_archives(root)

            self.assertEqual({f.name for f in found}, {"inner.jar", "inner2.zip"})

    def test_empty_when_no_archives_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Item.java").write_text("public class Item {}\n", encoding="utf-8")

            self.assertEqual(find_nested_archives(root), [])


class TestDiscoverSubmissionsNestedArchive(unittest.TestCase):
    def test_recovers_java_files_from_a_zip_wrapping_a_jar(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            submissions_dir = tmp_path / "submissions"
            submissions_dir.mkdir()
            extract_root = tmp_path / "_extracted"
            extract_root.mkdir()

            inner_jar_path = tmp_path / "inner.jar"
            with zipfile.ZipFile(inner_jar_path, "w") as inner_zf:
                inner_zf.writestr("logic/Item.java", "package logic;\npublic class Item {}\n")

            outer_path = submissions_dir / "12345678_w1_q2.zip"
            with zipfile.ZipFile(outer_path, "w") as outer_zf:
                outer_zf.write(inner_jar_path, "12345678_Q2W1.jar")

            submissions = discover_submissions(submissions_dir, extract_root)

            self.assertEqual(len(submissions), 1)
            sub = submissions[0]
            self.assertEqual(sub.student_id, "12345678_w1_q2")
            self.assertEqual([f.name for f in sub.java_files], ["Item.java"])
            self.assertTrue(any("nested archive" in n for n in sub.notes), sub.notes)
            # A .zip whose first unzip alone found no .java, even though digging
            # further into the nested jar did recover source - still an "improper
            # packaging" case for the 90% cap, independent of the outcome.
            self.assertTrue(sub.zip_needed_deeper_extraction)

    def test_does_not_touch_a_submission_that_already_has_real_java_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            submissions_dir = tmp_path / "submissions"
            submissions_dir.mkdir()
            extract_root = tmp_path / "_extracted"
            extract_root.mkdir()

            outer_path = submissions_dir / "87654321_w1_q2.jar"
            with zipfile.ZipFile(outer_path, "w") as outer_zf:
                outer_zf.writestr("logic/Item.java", "package logic;\npublic class Item {}\n")
                # a bundled "library jar" that isn't actually a valid zip - the fallback
                # must never even be attempted here, since real .java files are already found
                outer_zf.writestr("lib/junit-jupiter-api-5.14.0.jar", b"not a real jar, just bytes")

            submissions = discover_submissions(submissions_dir, extract_root)

            self.assertEqual(len(submissions), 1)
            sub = submissions[0]
            self.assertEqual([f.name for f in sub.java_files], ["Item.java"])
            self.assertEqual(sub.notes, [])
            # A .jar submitted directly (not wrapped in a .zip) never triggers the
            # "improper packaging" flag, even though it's technically also a zip
            # under the hood - only an actual .zip top-level submission can.
            self.assertFalse(sub.zip_needed_deeper_extraction)

    def test_still_reports_no_java_files_when_nested_archives_dont_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            submissions_dir = tmp_path / "submissions"
            submissions_dir.mkdir()
            extract_root = tmp_path / "_extracted"
            extract_root.mkdir()

            outer_path = submissions_dir / "11112222_w1_q2.zip"
            with zipfile.ZipFile(outer_path, "w") as outer_zf:
                outer_zf.writestr("README.txt", "no java here\n")

            submissions = discover_submissions(submissions_dir, extract_root)

            self.assertEqual(len(submissions), 1)
            sub = submissions[0]
            self.assertEqual(sub.java_files, [])
            self.assertTrue(any("contained no .java files" in n for n in sub.notes), sub.notes)
            self.assertTrue(sub.zip_needed_deeper_extraction)
            self.assertEqual(sub.class_search_root, extract_root / "0")


class TestDiscoverSubmissionsBareClass(unittest.TestCase):
    def test_bare_class_file_gets_its_own_isolated_search_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            submissions_dir = tmp_path / "submissions"
            submissions_dir.mkdir()
            extract_root = tmp_path / "_extracted"
            extract_root.mkdir()
            (submissions_dir / "55556666.class").write_bytes(b"not real bytecode")

            submissions = discover_submissions(submissions_dir, extract_root)

            self.assertEqual(len(submissions), 1)
            sub = submissions[0]
            self.assertEqual(sub.student_id, "55556666")
            self.assertEqual(sub.java_files, [])
            self.assertFalse(sub.zip_needed_deeper_extraction)
            self.assertIsNotNone(sub.class_search_root)
            self.assertTrue((sub.class_search_root / "55556666.class").exists())
            # Never searches the shared submissions_dir directly - that would risk
            # matching another student's same-named class.
            self.assertNotEqual(sub.class_search_root, submissions_dir)
            # A loose .class file is its own, separate, intentional path - never
            # the bare-source format violation.
            self.assertFalse(sub.not_an_archive)

    def test_folder_submission_gets_itself_as_search_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            submissions_dir = tmp_path / "submissions"
            submissions_dir.mkdir()
            extract_root = tmp_path / "_extracted"
            extract_root.mkdir()
            student_dir = submissions_dir / "77778888"
            student_dir.mkdir()
            (student_dir / "Item.class").write_bytes(b"")

            submissions = discover_submissions(submissions_dir, extract_root)

            self.assertEqual(len(submissions), 1)
            self.assertEqual(submissions[0].class_search_root, student_dir)

    def test_folder_submission_is_flagged_as_not_an_archive(self):
        """A directory sitting directly in submissions/ - e.g. an LMS bulk
        download bundling two individually-uploaded loose files together,
        such as Bot-<id>-<ts>.java and Part-<id>-<ts>.java - was never a
        packaged .zip/.jar, regardless of what's found inside it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            submissions_dir = tmp_path / "submissions"
            submissions_dir.mkdir()
            extract_root = tmp_path / "_extracted"
            extract_root.mkdir()
            student_dir = submissions_dir / "77778888"
            student_dir.mkdir()
            (student_dir / "Bot-1-999.java").write_text("public class Bot {}\n", encoding="utf-8")

            submissions = discover_submissions(submissions_dir, extract_root)

            self.assertTrue(submissions[0].not_an_archive)

    def test_loose_java_file_has_no_search_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            submissions_dir = tmp_path / "submissions"
            submissions_dir.mkdir()
            extract_root = tmp_path / "_extracted"
            extract_root.mkdir()
            (submissions_dir / "99990000.java").write_text(
                "public class Foo {}\n", encoding="utf-8"
            )

            submissions = discover_submissions(submissions_dir, extract_root)

            self.assertIsNone(submissions[0].class_search_root)
            self.assertTrue(submissions[0].not_an_archive)

    def test_zip_submission_is_not_flagged_as_bare_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            submissions_dir = tmp_path / "submissions"
            submissions_dir.mkdir()
            extract_root = tmp_path / "_extracted"
            extract_root.mkdir()
            zip_path = submissions_dir / "11112222.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("Foo.java", "public class Foo {}\n")

            submissions = discover_submissions(submissions_dir, extract_root)

            self.assertFalse(submissions[0].not_an_archive)


class TestCollectRequiredClassNames(unittest.TestCase):
    def test_collects_explicit_imports_ignoring_junit_and_jdk(self):
        with tempfile.TemporaryDirectory() as tmp:
            tf = Path(tmp) / "MisaShopTest2.java"
            tf.write_text(
                "package test.grader;\n"
                "import static org.junit.jupiter.api.Assertions.*;\n"
                "import org.junit.jupiter.api.Test;\n"
                "import application.MisaShop;\n"
                "import logic.Order;\n"
                "import logic.OrderItem;\n"
                "class MisaShopTest2 {}\n",
                encoding="utf-8",
            )

            names = collect_required_class_names([tf])

            self.assertEqual(names, {"MisaShop", "Order", "OrderItem"})

    def test_falls_back_to_constructor_calls_for_unnamed_package_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            tf = Path(tmp) / "TestStation.java"
            tf.write_text(
                "import org.junit.jupiter.api.Test;\n"
                "class TestStation {\n"
                "    @Test void t() { Station s = new Station(\"A\"); }\n"
                "}\n",
                encoding="utf-8",
            )

            names = collect_required_class_names([tf])

            self.assertIn("Station", names)

    def test_excludes_common_jdk_types_from_constructor_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            tf = Path(tmp) / "TestFoo.java"
            tf.write_text(
                "import org.junit.jupiter.api.Test;\n"
                "import java.util.ArrayList;\n"
                "class TestFoo {\n"
                "    @Test void t() { ArrayList<String> l = new ArrayList<>(); }\n"
                "}\n",
                encoding="utf-8",
            )

            names = collect_required_class_names([tf])

            self.assertNotIn("ArrayList", names)


class TestFindClassFiles(unittest.TestCase):
    def test_descends_into_build_output_dirs_unlike_find_java_files(self):
        """bin/target/build/out are exactly where a real compile puts its
        .class output (Eclipse/Maven/Gradle/IntelliJ respectively) - the
        .class-fallback feature this exists for must be able to see into
        them, unlike find_java_files which correctly stays out."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for build_dir in ("bin", "target", "build", "out"):
                d = root / build_dir / "logic"
                d.mkdir(parents=True)
                (d / "Item.class").write_bytes(b"")

            found = find_class_files(root)

            self.assertEqual(len(found), 4)
            for build_dir in ("bin", "target", "build", "out"):
                self.assertTrue(
                    any(build_dir in f.parts for f in found),
                    f"expected a match under {build_dir}/",
                )

    def test_still_skips_vcs_and_ide_metadata_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git" / "objects").mkdir(parents=True)
            (root / ".git" / "objects" / "Item.class").write_bytes(b"")
            (root / "logic").mkdir()
            (root / "logic" / "Item.class").write_bytes(b"")

            found = find_class_files(root)

            self.assertEqual(len(found), 1)
            self.assertIn("logic", found[0].parts)


class TestFindClassFallbackFiles(unittest.TestCase):
    def test_finds_top_level_and_inner_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logic").mkdir()
            (root / "logic" / "Item.class").write_bytes(b"")
            (root / "logic" / "Item$1.class").write_bytes(b"")
            (root / "org").mkdir()
            (root / "org" / "Assertions.class").write_bytes(b"")  # unrelated library class

            matches = find_class_fallback_files(root, {"Item", "Missing"})

            self.assertEqual(set(matches), {"Item"})
            self.assertEqual({f.name for f in matches["Item"]}, {"Item.class", "Item$1.class"})

    def test_returns_empty_dict_when_no_names_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_class_fallback_files(Path(tmp), set()), {})

    def test_finds_classes_inside_a_build_output_folder(self):
        """target/ (like bin/, build/, out/) is exactly where a real Maven
        compile puts its .class output - must be visible to the fallback
        search, not treated as noise to skip. See find_class_files."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target").mkdir()
            (root / "target" / "Item.class").write_bytes(b"")

            matches = find_class_fallback_files(root, {"Item"})

            self.assertEqual(set(matches), {"Item"})

    def test_skips_vcs_and_ide_metadata_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / ".git" / "Item.class").write_bytes(b"")

            matches = find_class_fallback_files(root, {"Item"})

            self.assertEqual(matches, {})


class TestResolveClassFallbackDest(unittest.TestCase):
    def test_strips_extra_prefix_matching_referenced_package(self):
        rel = Path("Q2_toStudent") / "logic" / "Item.class"

        result = resolve_class_fallback_dest(rel, {"logic", "application"})

        self.assertEqual(result, Path("logic") / "Item.class")

    def test_exact_package_match_is_unchanged(self):
        rel = Path("logic") / "Item.class"

        result = resolve_class_fallback_dest(rel, {"logic"})

        self.assertEqual(result, rel)

    def test_no_referenced_packages_returns_path_unchanged(self):
        rel = Path("Item.class")

        result = resolve_class_fallback_dest(rel, set())

        self.assertEqual(result, rel)

    def test_no_matching_suffix_returns_path_unchanged(self):
        rel = Path("com") / "example" / "Item.class"

        result = resolve_class_fallback_dest(rel, {"logic"})

        self.assertEqual(result, rel)


class TestInferUnnamedPackageClasses(unittest.TestCase):
    def test_unqualified_new_expr_is_unnamed_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            tf = Path(tmp) / "TestBot.java"
            tf.write_text(
                "import org.junit.jupiter.api.Test;\n"
                "class TestBot {\n"
                "    @Test void t() { Bot b = new Bot(); }\n"
                "}\n",
                encoding="utf-8",
            )

            self.assertEqual(infer_unnamed_package_classes([tf]), {"Bot"})

    def test_qualified_import_is_excluded_even_with_unqualified_new_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            tf = Path(tmp) / "ItemTest.java"
            tf.write_text(
                "import logic.Item;\n"
                "import org.junit.jupiter.api.Test;\n"
                "class ItemTest {\n"
                "    @Test void t() { Item i = new Item(); }\n"
                "}\n",
                encoding="utf-8",
            )

            self.assertEqual(infer_unnamed_package_classes([tf]), set())

    def test_common_jdk_type_never_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            tf = Path(tmp) / "T.java"
            tf.write_text(
                "class T { void t() { Object o = new ArrayList(); } }\n", encoding="utf-8"
            )

            self.assertEqual(infer_unnamed_package_classes([tf]), set())


class TestFilterUnusableFallbackMatches(unittest.TestCase):
    def _test_file(self, tmp_path: Path) -> Path:
        tf = tmp_path / "ItemTest.java"
        tf.write_text(
            "import org.junit.jupiter.api.Test;\n"
            "class ItemTest {\n"
            "    @Test void t() { Item i = new Item(); }\n"
            "}\n",
            encoding="utf-8",
        )
        return tf

    def test_drops_candidate_baked_under_mismatched_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            class_search_root = tmp_path / "submission"
            candidate = class_search_root / "main" / "java" / "Item.class"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"")  # relative_to() never reads file contents
            test_file = self._test_file(tmp_path)

            kept, notes = filter_unusable_fallback_matches(
                {"Item": [candidate]}, class_search_root, [test_file]
            )

            self.assertEqual(kept, {})
            self.assertEqual(len(notes), 1)
            self.assertIn("Item.class", notes[0])
            self.assertIn("main.java", notes[0])

    def test_keeps_candidate_found_at_unnamed_package_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            class_search_root = tmp_path / "submission"
            class_search_root.mkdir()
            candidate = class_search_root / "Item.class"
            candidate.write_bytes(b"")
            test_file = self._test_file(tmp_path)

            kept, notes = filter_unusable_fallback_matches(
                {"Item": [candidate]}, class_search_root, [test_file]
            )

            self.assertEqual(kept, {"Item": [candidate]})
            self.assertEqual(notes, [])

    def test_leaves_qualified_import_case_untouched(self):
        # Item required via `import logic.Item;` (not the unnamed-package case) -
        # this filter must not touch it even though the candidate is still nested;
        # resolve_class_fallback_dest's own trimming (or correct rejection at
        # compile time) owns that case, not this filter.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            class_search_root = tmp_path / "submission"
            candidate = class_search_root / "Q2_toStudent" / "logic" / "Item.class"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"")
            test_file = tmp_path / "ItemTest.java"
            test_file.write_text(
                "import logic.Item;\n"
                "import org.junit.jupiter.api.Test;\n"
                "class ItemTest {\n"
                "    @Test void t() { Item i = new Item(); }\n"
                "}\n",
                encoding="utf-8",
            )

            kept, notes = filter_unusable_fallback_matches(
                {"Item": [candidate]}, class_search_root, [test_file]
            )

            self.assertEqual(kept, {"Item": [candidate]})
            self.assertEqual(notes, [])


class TestCheckStructureBaselineClassFallback(unittest.TestCase):
    def test_covered_class_is_not_a_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            (build_dir / "Station.java").write_text("public class Station {}\n", encoding="utf-8")

            violations = check_structure_baseline(
                build_dir, official_names=set(), required_classes=["Station", "Ticket"],
                covered_by_class_fallback={"Ticket"},
            )

            self.assertEqual(violations, [])

    def test_uncovered_class_still_a_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)

            violations = check_structure_baseline(
                build_dir, official_names=set(), required_classes=["Station"],
                covered_by_class_fallback={"Ticket"},
            )

            self.assertEqual(len(violations), 1)
            self.assertIn("Station", violations[0])


class TestGradeStudentScoreCap(unittest.TestCase):
    """Integration-level: a real javac compile + real JUnit run, same pattern as
    TestGradeStudentStructureBaseline - the two examples that motivated this
    feature (a runnable-jar export with no .java, and a .zip wrapping a jar)
    are reproduced in miniature here rather than mocked."""

    def _junit_jar(self) -> Path:
        return find_junit_jar(Path(__file__).resolve().parent / "lib")

    def _compile_item_class(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        src = out_dir / "Item.java"
        src.write_text(
            "public class Item { public int getValue() { return 42; } }\n", encoding="utf-8"
        )
        result = subprocess.run(
            ["javac", "-d", str(out_dir), str(src)], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        src.unlink()  # only the .class remains - matching a real compiled-only submission

    def _compile_item_class_with_package(self, out_dir: Path, package: str) -> None:
        """Like _compile_item_class, but the source declares `package <package>;`
        first - javac itself nests the compiled Item.class under out_dir per the
        package (e.g. out_dir/main/java/Item.class), matching how a real IDE
        build output looks for a non-default-package class."""
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as src_tmp:
            src = Path(src_tmp) / "Item.java"
            src.write_text(
                f"package {package};\n"
                "public class Item { public int getValue() { return 42; } }\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["javac", "-d", str(out_dir), str(src)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def _item_test_file(self, tmp_path: Path) -> Path:
        tf = tmp_path / "ItemTest.java"
        tf.write_text(
            "import org.junit.jupiter.api.Test;\n"
            "import static org.junit.jupiter.api.Assertions.*;\n"
            "class ItemTest {\n"
            "    @Test void testGetValue() { assertEquals(42, new Item().getValue()); }\n"
            "}\n",
            encoding="utf-8",
        )
        return tf

    def test_missing_source_class_fallback_caps_at_50_percent(self):
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            class_dir = tmp_path / "precompiled"
            self._compile_item_class(class_dir)
            test_file = self._item_test_file(tmp_path)
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000001", "1", [], [], [test_file], ["ItemTest"], junit_jar, build_root,
                30, False, None, None, failed_build_root,
                class_search_root=class_dir,
                zip_needed_deeper_extraction=False,
                class_fallback_candidates={"Item"},
            )

            self.assertEqual(row["compiled"], "yes")
            self.assertEqual(row["tests_passed"], 1)
            self.assertEqual(row["uncapped_score"], 1)
            self.assertEqual(row["score"], 0.5)
            self.assertEqual(row["score_cap"], "50%")
            self.assertIn("SCORE CAPPED AT 50%", row["notes"])
            self.assertIn("Item", row["notes"])

    def test_class_fallback_under_extra_wrapper_directory_still_resolves(self):
        """Covers the case where an extra directory level is a pure extraction/
        packaging artifact (e.g. a student zipped their whole Eclipse project,
        so the true classpath root is ProjectName/bin/logic/Item.class) rather
        than part of the class's actual compiled package - Item.class here is
        genuinely `package logic;`, just sitting one level deeper on disk than
        find_class_fallback_files' caller used to assume. Before
        resolve_class_fallback_dest, the wrapper directory was preserved
        verbatim into classes/, landing the file outside package `logic` on
        javac's classpath ("cannot find symbol") even though nothing was
        actually wrong with the class.

        NOT what this fix rescues: a real grading run turned up two students
        whose precompiled classes were genuinely compiled under a
        `Q2_toStudent.logic` package (confirmed via javap - baked into the
        classfile's own this_class entry, e.g. `class Q2_toStudent.logic.Item`,
        not just a folder name), the .class equivalent of the
        Q1_toStudent.application example in strip_package_declaration's
        docstring. Unlike source text, a compiled class's internal identity
        can't be rewritten by relocating the file, so those two correctly
        still fail to compile - now with a "class file contains wrong class"
        diagnostic instead of a misleading "cannot find symbol", but the same
        (correct) failing grade."""
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            compiled_root = tmp_path / "compiled"
            pkg_src_dir = compiled_root / "logic"
            pkg_src_dir.mkdir(parents=True)
            src = pkg_src_dir / "Item.java"
            src.write_text(
                "package logic;\npublic class Item { public int getValue() { return 42; } }\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["javac", "-d", str(compiled_root), str(src)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            class_search_root = tmp_path / "submission"
            shutil.copytree(compiled_root / "logic", class_search_root / "Q2_toStudent" / "logic")

            test_file = tmp_path / "ItemTest.java"
            test_file.write_text(
                "import logic.Item;\n"
                "import org.junit.jupiter.api.Test;\n"
                "import static org.junit.jupiter.api.Assertions.*;\n"
                "class ItemTest {\n"
                "    @Test void testGetValue() { assertEquals(42, new Item().getValue()); }\n"
                "}\n",
                encoding="utf-8",
            )
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000005", "1", [], [], [test_file], ["ItemTest"], junit_jar, build_root,
                30, False, None, None, failed_build_root,
                class_search_root=class_search_root,
                zip_needed_deeper_extraction=False,
                class_fallback_candidates={"Item"},
            )

            self.assertEqual(row["compiled"], "yes", row["notes"])
            self.assertEqual(row["tests_passed"], 1)
            self.assertEqual(row["score"], 0.5)
            self.assertEqual(row["score_cap"], "50%")

    def test_duplicate_class_fallback_matches_are_flagged_not_silently_dropped(self):
        """Two unrelated copies of Item.class under different wrapper prefixes
        both trim down to the same classpath destination (logic/Item.class) via
        resolve_class_fallback_dest. Silently letting the later one win would
        hide a real ambiguity from whoever reviews this student's row."""
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            compiled_root = tmp_path / "compiled"
            pkg_src_dir = compiled_root / "logic"
            pkg_src_dir.mkdir(parents=True)
            src = pkg_src_dir / "Item.java"
            src.write_text(
                "package logic;\npublic class Item { public int getValue() { return 42; } }\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["javac", "-d", str(compiled_root), str(src)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            class_search_root = tmp_path / "submission"
            shutil.copytree(compiled_root / "logic", class_search_root / "WrapperA" / "logic")
            shutil.copytree(compiled_root / "logic", class_search_root / "WrapperB" / "logic")

            test_file = tmp_path / "ItemTest.java"
            test_file.write_text(
                "import logic.Item;\n"
                "import org.junit.jupiter.api.Test;\n"
                "import static org.junit.jupiter.api.Assertions.*;\n"
                "class ItemTest {\n"
                "    @Test void testGetValue() { assertEquals(42, new Item().getValue()); }\n"
                "}\n",
                encoding="utf-8",
            )
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000006", "1", [], [], [test_file], ["ItemTest"], junit_jar, build_root,
                30, False, None, None, failed_build_root,
                class_search_root=class_search_root,
                zip_needed_deeper_extraction=False,
                class_fallback_candidates={"Item"},
            )

            self.assertEqual(row["compiled"], "yes", row["notes"])
            self.assertEqual(row["tests_passed"], 1)
            self.assertIn("WARNING: multiple .class files resolved to the same classpath", row["notes"])

    def test_zip_needing_deeper_extraction_caps_at_90_percent_even_with_source(self):
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            item_file = src_dir / "Item.java"
            item_file.write_text(
                "public class Item { public int getValue() { return 42; } }\n", encoding="utf-8"
            )
            test_file = self._item_test_file(tmp_path)
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000002", "1", [item_file], [], [test_file], ["ItemTest"], junit_jar, build_root,
                30, False, None, None, failed_build_root,
                class_search_root=None,
                zip_needed_deeper_extraction=True,
                class_fallback_candidates={"Item"},
            )

            self.assertEqual(row["compiled"], "yes")
            self.assertEqual(row["tests_passed"], 1)
            self.assertEqual(row["uncapped_score"], 1)
            self.assertEqual(row["score"], 0.9)
            self.assertEqual(row["score_cap"], "90%")
            self.assertIn("SCORE CAPPED AT 90%", row["notes"])
            self.assertIn("nested/deeper archive", row["notes"])

    def test_both_conditions_combine_multiplicatively_to_45_percent(self):
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            class_dir = tmp_path / "precompiled"
            self._compile_item_class(class_dir)
            test_file = self._item_test_file(tmp_path)
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000003", "1", [], [], [test_file], ["ItemTest"], junit_jar, build_root,
                30, False, None, None, failed_build_root,
                class_search_root=class_dir,
                zip_needed_deeper_extraction=True,
                class_fallback_candidates={"Item"},
            )

            self.assertEqual(row["uncapped_score"], 1)
            self.assertEqual(row["score"], 0.45)
            self.assertEqual(row["score_cap"], "45%")

    def test_no_cap_when_source_is_present_and_no_deeper_extraction_needed(self):
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            item_file = src_dir / "Item.java"
            item_file.write_text(
                "public class Item { public int getValue() { return 42; } }\n", encoding="utf-8"
            )
            test_file = self._item_test_file(tmp_path)
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000004", "1", [item_file], [], [test_file], ["ItemTest"], junit_jar, build_root,
                30, False, None, None, failed_build_root,
                class_fallback_candidates={"Item"},
            )

            self.assertEqual(row["uncapped_score"], 1)
            self.assertEqual(row["score"], 1)
            self.assertEqual(row["score_cap"], "")
            self.assertNotIn("SCORE CAPPED", row["notes"])

    def test_class_missing_entirely_with_no_student_files_at_all(self):
        # No .java anywhere and nothing to search for a .class fallback either -
        # stays the same "no .java source files found" short-circuit as before
        # this feature existed, never a false 50%/90% cap.
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            test_file = self._item_test_file(tmp_path)
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000005", "1", [], [], [test_file], ["ItemTest"], junit_jar, build_root,
                30, False, None, None, failed_build_root,
                class_fallback_candidates={"Item"},
            )

            self.assertEqual(row["compiled"], "no")
            self.assertIn("no .java source files found", row["notes"])
            self.assertEqual(row["score_cap"], "")

    def test_class_missing_entirely_but_other_source_present_is_a_compile_error(self):
        # Some unrelated .java IS present (so compilation is actually attempted),
        # but the required class Item has neither .java nor a matching .class
        # anywhere - a normal "cannot find symbol" compile error, score 0, and
        # never treated as a class-fallback (no cap fields set).
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            unrelated_file = src_dir / "Helper.java"
            unrelated_file.write_text("public class Helper {}\n", encoding="utf-8")
            test_file = self._item_test_file(tmp_path)
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000006", "1", [unrelated_file], [], [test_file], ["ItemTest"], junit_jar,
                build_root, 30, False, None, None, failed_build_root,
                class_fallback_candidates={"Item"},
            )

            self.assertEqual(row["compiled"], "no")
            self.assertIn("COMPILE ERROR", row["notes"])
            self.assertEqual(row["score_cap"], "")

    def test_fallback_class_baked_under_mismatched_package_is_a_clear_structure_error(self):
        """The real case this feature targets: a runnable-jar export whose
        Item.class genuinely declares `package main.java;` (a common IDE
        default in this course - see strip_package_declaration), alongside
        some unrelated .java source, for a test that needs Item unqualified.
        Before this fix, this candidate got seeded into classes/ anyway and
        only failed once javac ran, as a generic "cannot find symbol" -
        indistinguishable in the CSV from any other compile error. Now it's
        dropped up front with a specific note, and - since Item is also in
        required_classes - correctly reported as a STRUCTURE ERROR instead."""
        junit_jar = self._junit_jar()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            class_search_root = tmp_path / "submission"
            self._compile_item_class_with_package(class_search_root, "main.java")
            unrelated_file = class_search_root / "Helper.java"
            unrelated_file.write_text("public class Helper {}\n", encoding="utf-8")
            test_file = self._item_test_file(tmp_path)
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000007", "1", [unrelated_file], [], [test_file], ["ItemTest"], junit_jar,
                build_root, 30, False, None, ["Item"], failed_build_root,
                class_search_root=class_search_root,
                zip_needed_deeper_extraction=False,
                class_fallback_candidates={"Item"},
            )

            self.assertEqual(row["compiled"], "no")
            self.assertIn("STRUCTURE ERROR: missing required class Item", row["notes"])
            self.assertIn("compiled under package 'main.java'", row["notes"])
            self.assertNotIn("cannot find symbol", row["notes"])


class TestGradeStudentNotAnArchive(unittest.TestCase):
    def test_bare_java_source_is_rejected_even_though_it_would_pass(self):
        """not_an_archive=True must short-circuit before compiling at all - proven
        here by giving it genuinely correct, compilable source (matching a real
        case: a student's Bot.java/Part.java were fine, but bundled by the LMS as
        loose uploads instead of a .zip/.jar) and confirming it still scores 0
        with a STRUCTURE ERROR, never actually reaching javac."""
        junit_jar = find_junit_jar(Path(__file__).resolve().parent / "lib")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            item_file = tmp_path / "Item.java"
            item_file.write_text(
                "public class Item { public int getValue() { return 42; } }\n",
                encoding="utf-8",
            )
            test_file = tmp_path / "ItemTest.java"
            test_file.write_text(
                "import org.junit.jupiter.api.Test;\n"
                "import static org.junit.jupiter.api.Assertions.*;\n"
                "class ItemTest {\n"
                "    @Test void testGetValue() { assertEquals(42, new Item().getValue()); }\n"
                "}\n",
                encoding="utf-8",
            )
            build_root = tmp_path / "build_tmp"
            build_root.mkdir()
            failed_build_root = tmp_path / "failed_builds"
            failed_build_root.mkdir()

            row = grade_student(
                "10000008", "1", [item_file], [], [test_file], ["ItemTest"], junit_jar,
                build_root, 30, False, None, None, failed_build_root,
                not_an_archive=True,
            )

            self.assertEqual(row["compiled"], "no")
            self.assertEqual(row["score"], 0)
            self.assertIn("STRUCTURE ERROR", row["notes"])
            self.assertIn("bare .java source", row["notes"])


class TestCheckOutputWritable(unittest.TestCase):
    def test_none_for_nonexistent_path_and_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results" / "grades.csv"

            self.assertIsNone(check_output_writable(out_path))
            self.assertTrue(out_path.exists())

    def test_existing_file_content_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "grades.csv"
            out_path.write_text("previous run's data\n", encoding="utf-8")

            self.assertIsNone(check_output_writable(out_path))

            self.assertEqual(out_path.read_text(encoding="utf-8"), "previous run's data\n")

    def test_returns_message_when_open_raises_oserror(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "grades.csv"
            with mock.patch("builtins.open", side_effect=PermissionError("in use by another process")):
                problem = check_output_writable(out_path)

            self.assertIsNotNone(problem)
            self.assertIn(str(out_path), problem)
            self.assertIn("Excel", problem)


class TestWriteCsvNewColumns(unittest.TestCase):
    def test_includes_uncapped_score_and_score_cap_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "grades.csv"
            rows = [{
                "student_id": "1", "compiled": "yes", "tests_passed": 1, "tests_total": 2,
                "score": 0.5, "max_score": 1, "uncapped_score": 1, "score_cap": "50%",
                "passed_tests": "", "failed_tests": "", "failure_details": "",
                "notes": "SCORE CAPPED AT 50%: ...",
            }]

            write_csv(rows, out_path)

            content = out_path.read_text(encoding="utf-8")
            header = content.splitlines()[0]
            self.assertIn("uncapped_score", header)
            self.assertIn("score_cap", header)
            self.assertIn("0.5", content)
            self.assertIn("50%", content)

    def test_blank_score_cap_for_an_uncapped_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "grades.csv"
            rows = [{
                "student_id": "1", "compiled": "yes", "tests_passed": 2, "tests_total": 2,
                "score": 2, "max_score": 2, "uncapped_score": 2, "score_cap": "",
                "passed_tests": "", "failed_tests": "", "failure_details": "",
                "notes": "",
            }]

            write_csv(rows, out_path)

            lines = out_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[1].split(","), [
                "1", "yes", "2", "2", "2", "2", "2", "", "", "", "", "",
            ])


if __name__ == "__main__":
    unittest.main()
