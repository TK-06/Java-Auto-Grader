import argparse
import contextlib
import csv
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import verify_student_run

GRADING_DIR = Path(__file__).resolve().parent


class TestSelfCheckExtractionFailure(unittest.TestCase):
    """An archive that could not be unpacked says nothing about what is in it.
    The self-check used to report it as NO TEST FILE and tell the student to put
    the given test into their project - advice about a file that may well be
    there (W14 Q2: the jar held its test and its source; the grading machine's
    path length was the problem)."""

    def test_extraction_failure_is_reported_as_could_not_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            submissions = tmp_path / "submissions"
            submissions.mkdir()
            with zipfile.ZipFile(submissions / "6800000021.jar", "w") as zf:
                zf.writestr("sort/SelectionSort.java", "package sort;\npublic class SelectionSort {}\n")
                zf.writestr("test/SelectionSortTest.java",
                            "package test;\nimport org.junit.jupiter.api.Test;\nclass SelectionSortTest {}\n")
            out_path = tmp_path / "results" / "verify_grades.csv"
            out_path.parent.mkdir()
            args = argparse.Namespace(
                submissions=str(submissions), config=str(tmp_path / "no_config"),
                lib=str(GRADING_DIR / "lib"), out=str(out_path), test_file=None,
                timeout=30, keep_build=False,
            )

            stdout = io.StringIO()
            cwd = os.getcwd()
            os.chdir(tmp_path)
            try:
                with mock.patch.object(zipfile.ZipFile, "extractall",
                                       side_effect=FileNotFoundError(2, "No such file or directory")), \
                        contextlib.redirect_stdout(stdout):
                    verify_student_run.student_main(args)
            finally:
                os.chdir(cwd)

            printed = stdout.getvalue()
            self.assertIn("COULD NOT EXTRACT", printed)
            self.assertNotIn("NO TEST FILE", printed)
            row = next(csv.DictReader(out_path.open(encoding="utf-8")))
            self.assertTrue(row["notes"].startswith("could not extract"), row["notes"])
            self.assertNotIn("no JUnit test file", row["notes"])


if __name__ == "__main__":
    unittest.main()
