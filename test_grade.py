import tempfile
import unittest
from pathlib import Path

from grade import prepare_build_dir, strip_imports_of_packages, strip_package_declaration


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

        new_text, package_name = strip_package_declaration(text)

        self.assertEqual(package_name, "main.java")
        self.assertNotIn("package", new_text)
        self.assertIn("public class Bot {", new_text)

    def test_leaves_text_unchanged_when_no_package_declaration(self):
        text = "public class Bot {\n}\n"

        new_text, package_name = strip_package_declaration(text)

        self.assertIsNone(package_name)
        self.assertEqual(new_text, text)

    def test_leaves_package_declaration_when_in_keep_set(self):
        text = "package application;\n\npublic class CPTSMachine {\n}\n"

        new_text, package_name = strip_package_declaration(text, keep_packages={"application"})

        self.assertIsNone(package_name)
        self.assertEqual(new_text, text)


if __name__ == "__main__":
    unittest.main()
