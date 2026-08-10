import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from grade import (
    RMTREE_RETRY_ATTEMPTS,
    CompileResult,
    bare_student_id,
    check_structure_baseline,
    collect_test_results,
    compile_submission_with_fallback,
    discover_submissions,
    find_java_files,
    find_junit_jar,
    find_nested_archives,
    grade_student,
    load_structure_baseline,
    prepare_build_dir,
    rewrite_imports_of_renamed_packages,
    rmtree_with_retry,
    strip_imports_of_packages,
    strip_package_declaration,
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
            student_id, java_files, notes = submissions[0]
            self.assertEqual(student_id, "12345678_w1_q2")
            self.assertEqual([f.name for f in java_files], ["Item.java"])
            self.assertTrue(any("nested archive" in n for n in notes), notes)

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
            student_id, java_files, notes = submissions[0]
            self.assertEqual([f.name for f in java_files], ["Item.java"])
            self.assertEqual(notes, [])

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
            student_id, java_files, notes = submissions[0]
            self.assertEqual(java_files, [])
            self.assertTrue(any("contained no .java files" in n for n in notes), notes)


if __name__ == "__main__":
    unittest.main()
