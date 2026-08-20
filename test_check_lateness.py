import unittest

from check_lateness import apply_late_caps, parse_cap_fraction


def grades_row(**overrides):
    row = {
        "student_id": "6738201021",
        "compiled": "yes",
        "tests_passed": "10",
        "tests_total": "11",
        "score": "10",
        "max_score": "11",
        "uncapped_score": "10",
        "score_cap": "",
        "passed_tests": "",
        "failed_tests": "",
        "failure_details": "",
        "notes": "",
    }
    row.update(overrides)
    return row


def late_row(days_late, multiplier):
    return {"days_late": days_late, "multiplier": multiplier}


class TestParseCapFraction(unittest.TestCase):
    def test_blank_means_no_cap(self):
        self.assertEqual(parse_cap_fraction(""), 1.0)

    def test_percent_string(self):
        self.assertEqual(parse_cap_fraction("50%"), 0.5)

    def test_zero_percent(self):
        self.assertEqual(parse_cap_fraction("0%"), 0.0)


class TestApplyLateCaps(unittest.TestCase):
    def test_on_time_student_untouched(self):
        rows = [grades_row(student_id="111", score="10", uncapped_score="10")]
        late_by_student = {"111": late_row(days_late=0, multiplier=1.0)}

        mcv_rows, report = apply_late_caps(rows, late_by_student)

        self.assertEqual(mcv_rows[0]["score"], "10")
        self.assertEqual(report, [])

    def test_student_not_in_zip_untouched(self):
        rows = [grades_row(student_id="111", score="10")]

        mcv_rows, report = apply_late_caps(rows, late_by_student={})

        self.assertEqual(mcv_rows[0]["score"], "10")
        self.assertEqual(report, [])

    def test_late_with_no_other_cap_docks_normally(self):
        # 1 day late, no other cap: 90% of 11 = 9.9.
        rows = [grades_row(student_id="111", score="11", uncapped_score="11",
                            max_score="11", score_cap="")]
        late_by_student = {"111": late_row(days_late=1, multiplier=0.9)}

        mcv_rows, report = apply_late_caps(rows, late_by_student)

        self.assertAlmostEqual(mcv_rows[0]["score"], 9.9)
        self.assertEqual(len(report), 1)

    def test_existing_cap_stricter_than_late_penalty_wins(self):
        # .class-only submission capped at 50% (5.5/11), also 1 day late
        # (which alone would only cap at 90%). Stricter cap (50%) must win --
        # NOT 0.5 * 0.9 = 45%.
        rows = [grades_row(student_id="111", score="5.5", uncapped_score="11",
                            max_score="11", score_cap="50%")]
        late_by_student = {"111": late_row(days_late=1, multiplier=0.9)}

        mcv_rows, report = apply_late_caps(rows, late_by_student)

        self.assertAlmostEqual(float(mcv_rows[0]["score"]), 5.5)
        self.assertEqual(report, [])  # no change from the existing 5.5 -> nothing to report

    def test_late_penalty_stricter_than_existing_cap_wins(self):
        # 90% nested-archive cap, but 3 days late (70%) is stricter.
        rows = [grades_row(student_id="111", score="9.9", uncapped_score="11",
                            max_score="11", score_cap="90%")]
        late_by_student = {"111": late_row(days_late=3, multiplier=0.7)}

        mcv_rows, report = apply_late_caps(rows, late_by_student)

        self.assertAlmostEqual(mcv_rows[0]["score"], 7.7)
        self.assertEqual(len(report), 1)

    def test_ta_override_row_never_auto_adjusted(self):
        rows = [grades_row(student_id="111", score="0", uncapped_score="11",
                            max_score="11", score_cap="",
                            notes="TA OVERRIDE: academic integrity violation, forced to 0")]
        late_by_student = {"111": late_row(days_late=2, multiplier=0.8)}

        mcv_rows, report = apply_late_caps(rows, late_by_student)

        self.assertEqual(mcv_rows[0]["score"], "0")
        self.assertEqual(len(report), 1)
        self.assertIn("TA OVERRIDE", report[0])

    def test_matches_by_bare_student_id_stripping_question_tag(self):
        rows = [grades_row(student_id="111_w2_q1", score="11", uncapped_score="11",
                            max_score="11", score_cap="")]
        late_by_student = {"111": late_row(days_late=1, multiplier=0.9)}

        mcv_rows, report = apply_late_caps(rows, late_by_student)

        self.assertAlmostEqual(mcv_rows[0]["score"], 9.9)

    def test_uncapped_score_bounds_the_result(self):
        # A student who already lost points on tests (uncapped 3/11) and is
        # also late shouldn't be pushed ABOVE what they actually earned.
        rows = [grades_row(student_id="111", score="3", uncapped_score="3",
                            max_score="11", score_cap="")]
        late_by_student = {"111": late_row(days_late=1, multiplier=0.9)}

        mcv_rows, report = apply_late_caps(rows, late_by_student)

        self.assertAlmostEqual(float(mcv_rows[0]["score"]), 3.0)
        self.assertEqual(report, [])

    def test_result_is_rounded_to_avoid_float_noise(self):
        # 11 * 0.7 == 7.699999999999999 in raw floating point -- the CSV
        # written to mcvScore.csv (uploaded as-is) must show a clean 7.7.
        rows = [grades_row(student_id="111", score="11", uncapped_score="11",
                            max_score="11", score_cap="")]
        late_by_student = {"111": late_row(days_late=3, multiplier=0.7)}

        mcv_rows, report = apply_late_caps(rows, late_by_student)

        self.assertEqual(mcv_rows[0]["score"], 7.7)

    def test_zero_percent_cap_stays_zero_regardless_of_lateness(self):
        rows = [grades_row(student_id="111", score="0", uncapped_score="2.5",
                            max_score="11", score_cap="0%",
                            notes="SCORE CAPPED AT 0%: matches the unedited starter template")]
        late_by_student = {"111": late_row(days_late=5, multiplier=0.5)}

        mcv_rows, report = apply_late_caps(rows, late_by_student)

        self.assertAlmostEqual(float(mcv_rows[0]["score"]), 0.0)


if __name__ == "__main__":
    unittest.main()
