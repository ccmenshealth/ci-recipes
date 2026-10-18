#!/usr/bin/env python3
"""Tests for identifier-guard.py (stdlib unittest; python3 -m unittest discover -s identifier-guard/tests).

One table row per finding of the 2026-10-17 hardening plan (F1-F4, G1-G5). Every literal in this file is one of the
allowed test-data shapes (ROE/DOE/POE personas, 555-01xx and area-code-555 phones, the 4711 block, CCM-471NN, the
999 sentinel, 1900-01-01 / 1970-01-01). Values the guard must FLAG are assembled at run time from repeated digits
(`d("2", 3)` -> three 2s) so that no identifier-shaped literal sits in this file.

GUARD=/path/to/identifier-guard.py runs the same table against another copy of the script (used to show the rows
failing against the pre-fix script).
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.environ.get("GUARD") or os.path.join(HERE, "..", "identifier-guard.py")


def load_guard_from(path):
    spec = importlib.util.spec_from_file_location("identifier_guard_" + str(abs(hash(path))), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_guard():
    spec = importlib.util.spec_from_file_location("identifier_guard", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = load_guard()


def d(digit, n):
    """n copies of one digit: an obviously constructed value, never a real one."""
    return digit * n


def patterns(path, text):
    """Pattern names the guard reports for one line at one path."""
    findings = []
    guard.check_line(path, 1, text, findings)
    return sorted(k for _, _, k in findings)


SRC = "src/app.py"                     # a production path: every pattern is checked
THIS_YEAR = date.today().year
RECENT = THIS_YEAR - 4                 # a 2020+ birth year: a child, flagged in value position
ADULT = THIS_YEAR - 40                 # a plausible adult birth year: flagged everywhere, as today

# --- the flagged-value constructors (assembled at run time, see module docstring) ---
EIGHT = d("2", 8)                                            # an 8-digit record number
TEN = d("2", 3) + d("3", 3) + d("4", 4)                       # ten contiguous digits, area/exchange in [2-9]
SPACED = d("2", 3) + " " + d("3", 3) + " " + d("4", 4)        # the same number, space-separated 3-3-4
DASHED = d("2", 3) + "-" + d("3", 3) + "-" + d("4", 4)        # and dashed (flagged today, must stay flagged)
SSN_DASHED = d("2", 3) + "-" + d("3", 2) + "-" + d("4", 4)
SSN_PLAIN = d("2", 3) + d("3", 2) + d("4", 4)
SSN_SPACED = d("2", 3) + " " + d("3", 2) + " " + d("4", 4)


class Baseline(unittest.TestCase):
    """What the guard already does and must keep doing."""

    def test_mrn_label_is_flagged_and_sentinel_4711_is_not(self):
        self.assertEqual(patterns(SRC, "MRN: " + EIGHT), ["mrn"])
        self.assertEqual(patterns(SRC, "MRN: 47110001"), [])

    def test_dashed_phone_is_flagged_and_555_01xx_is_not(self):
        self.assertEqual(patterns(SRC, "call " + DASHED), ["phone"])
        self.assertEqual(patterns(SRC, "call 555-0142"), [])

    def test_persona_and_ccm_471nn_are_synthetic(self):
        self.assertEqual(patterns(SRC, "ROE, RICHARD 47110001 CCM-47101"), [])

    def test_adult_dob_is_flagged_in_any_position(self):
        self.assertEqual(patterns(SRC, "DOB: %d-03-14" % ADULT), ["dob"])
        self.assertEqual(patterns(SRC, "dob added %d-03-14" % ADULT), ["dob"])

    def test_placeholder_dobs_are_exempt(self):
        self.assertEqual(patterns(SRC, "DOB: 1900-01-01"), [])
        self.assertEqual(patterns(SRC, "DOB: 1970-01-01"), [])
        self.assertEqual(patterns(SRC, "DOB: 01/01/1970"), [])

    def test_marker_words_exempt_the_line(self):
        self.assertEqual(patterns(SRC, "MRN: " + EIGHT + "  # synthetic"), [])


class F1_FinCaseInsensitive(unittest.TestCase):
    def test_fin_lowercase_and_titlecase_are_flagged_like_mrn(self):
        self.assertEqual(patterns(SRC, "FIN: " + EIGHT), ["mrn"])      # today
        self.assertEqual(patterns(SRC, "fin: " + EIGHT), ["mrn"])      # F1
        self.assertEqual(patterns(SRC, "Fin: " + EIGHT), ["mrn"])      # F1
        self.assertEqual(patterns(SRC, "encounterFin = " + EIGHT), ["mrn"])

    def test_fin_as_part_of_a_word_is_not(self):
        self.assertEqual(patterns(SRC, "final = " + EIGHT), [])
        self.assertEqual(patterns(SRC, "define " + EIGHT), [])


class F2_SpacedAndContiguousPhones(unittest.TestCase):
    def test_space_separated_3_3_4_is_flagged(self):
        self.assertEqual(patterns(SRC, "reach me at " + SPACED), ["phone"])

    def test_contiguous_ten_digits_flagged_only_after_a_phone_label_within_40_chars(self):
        self.assertEqual(patterns(SRC, "phone: " + TEN), ["phone"])
        self.assertEqual(patterns(SRC, '"mobileNumber": "' + TEN + '"'), ["phone"])
        self.assertEqual(patterns(SRC, "fax = '" + TEN + "'"), ["phone"])
        self.assertEqual(patterns(SRC, "const id = " + TEN), [])                 # no label: an id, not a phone
        self.assertEqual(patterns(SRC, "phone " + ("x" * 41) + " " + TEN), [])   # label too far away

    def test_spaced_number_with_area_or_exchange_outside_2_9_is_not_a_phone_shape(self):
        shape = guard.PATTERNS["phone"][0]          # the shape itself, before G5 judges the digits
        self.assertIsNotNone(shape.search("ref " + SPACED))
        self.assertIsNone(shape.search("ref " + d("1", 3) + " " + d("3", 3) + " " + d("4", 4)))
        self.assertIsNone(shape.search("ref " + d("2", 3) + " " + d("0", 3) + " " + d("4", 4)))

    def test_label_followed_by_more_than_ten_digits_is_not_a_phone(self):
        self.assertEqual(patterns(SRC, "phone: " + d("2", 11)), [])
        self.assertEqual(patterns(SRC, "phone: " + d("2", 3) + d("3", 3) + d("4", 4) + "." + d("5", 2)), [])

    def test_synthetic_token_in_the_gap_does_not_exempt_the_number(self):
        self.assertEqual(patterns(SRC, "phone ref 47110001 then " + TEN), ["phone"])


class F3_RecentDobYears(unittest.TestCase):
    def test_recent_year_in_value_position_is_flagged(self):
        self.assertEqual(patterns(SRC, "DOB: %d-03-14" % RECENT), ["dob"])
        self.assertEqual(patterns(SRC, '"dob": "03/14/%d"' % RECENT), ["dob"])
        self.assertEqual(patterns(SRC, "dateOfBirth=%d-03-14" % RECENT), ["dob"])

    def test_recent_year_after_prose_is_not(self):
        self.assertEqual(patterns(SRC, "dob added %d-09-01" % THIS_YEAR), [])
        self.assertEqual(patterns(SRC, "// birth date column renamed %d-03-14" % RECENT), [])

    def test_impossible_years_are_exempt(self):
        self.assertEqual(patterns(SRC, "DOB: %d-03-14" % (THIS_YEAR + 1)), [])
        self.assertEqual(patterns(SRC, "DOB: %d-12-31" % (1900 - 1)), [])

    def test_1900s_dates_other_than_the_placeholder_are_flagged(self):
        self.assertEqual(patterns(SRC, "DOB: %d-03-14" % (1900 + 5)), ["dob"])


class F4_FilesModeFailsClosed(unittest.TestCase):
    def run_guard(self, *args):
        return subprocess.run([sys.executable, GUARD, *args], capture_output=True, text=True)

    def test_unreadable_path_prints_the_error_and_exits_2(self):
        r = self.run_guard("--files", os.path.join(HERE, "no-such-file.txt"))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no-such-file.txt", r.stdout + r.stderr)
        self.assertNotIn("clean", r.stdout)

    def test_binary_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "form.pdf")
            with open(p, "wb") as f:
                f.write(b"%PDF-1.4\x00" + ("MRN: " + EIGHT + "\n").encode() * 3)
            r = self.run_guard("--files", p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_lines_over_2000_chars_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "bundle.js")
            with open(p, "w") as f:
                f.write("x" * 2001 + " MRN: " + EIGHT + "\n")
                f.write("short line, MRN: 47110001\n")
            r = self.run_guard("--files", p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_text_file_with_a_hit_still_fails_with_exit_1(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "notes.md")
            with open(p, "w") as f:
                f.write("MRN: " + EIGHT + "\n")
            r = self.run_guard("--files", p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("notes.md:1 mrn", r.stdout)
        self.assertNotIn(EIGHT, r.stdout)          # never prints the matched text


class G1_MonthNameDobs(unittest.TestCase):
    def test_month_name_forms_are_flagged(self):
        self.assertEqual(patterns(SRC, "DOB: March 14, %d" % ADULT), ["dob"])
        self.assertEqual(patterns(SRC, "DOB: 14 Mar %d" % ADULT), ["dob"])
        self.assertEqual(patterns(SRC, "DOB: 14-Mar-%d" % ADULT), ["dob"])      # Cerner style
        self.assertEqual(patterns(SRC, "DOB: Mar-14-%d" % ADULT), ["dob"])
        self.assertEqual(patterns(SRC, "Date of birth: Sept 3 %d" % ADULT), ["dob"])

    def test_month_name_placeholder_is_exempt(self):
        self.assertEqual(patterns(SRC, "DOB: January 1, 1970"), [])


class G2_LabelledSsnWithoutDashes(unittest.TestCase):
    def test_dashed_ssn_is_flagged_as_today(self):
        self.assertEqual(patterns(SRC, "x " + SSN_DASHED), ["ssn"])

    def test_labelled_ssn_without_dashes_is_flagged(self):
        self.assertEqual(patterns(SRC, "SSN: " + SSN_PLAIN), ["ssn"])
        self.assertEqual(patterns(SRC, "ssn " + SSN_SPACED), ["ssn"])
        self.assertEqual(patterns(SRC, "social security number " + SSN_PLAIN), ["ssn"])
        self.assertEqual(patterns(SRC, "patient_ssn = " + SSN_PLAIN), ["ssn"])
        self.assertEqual(patterns(SRC, "patientSsn: " + SSN_PLAIN), ["ssn"])

    def test_bare_nine_digits_without_a_label_are_not(self):
        self.assertEqual(patterns(SRC, "const id = " + SSN_PLAIN), [])


class G3_FixtureFileNames(unittest.TestCase):
    def test_files_named_fixtures_skip_record_number_and_dob_checks(self):
        self.assertEqual(patterns("webapp/src/chartMerge/testFixtures.ts", "dob: %d-03-14" % ADULT), [])
        self.assertEqual(patterns("src/labFixtures.ts", "MRN: " + EIGHT), [])

    def test_fixture_files_still_check_phones_names_and_ssns(self):
        self.assertEqual(patterns("src/labFixtures.ts", "fax " + DASHED), ["phone"])
        self.assertEqual(patterns("src/labFixtures.ts", "x " + SSN_DASHED), ["ssn"])

    def test_a_production_file_is_still_fully_checked(self):
        self.assertEqual(patterns("src/chartMerge/merge.ts", "dob: %d-03-14" % ADULT), ["dob"])


class G4_UnifiedVariant(unittest.TestCase):
    def test_999_sentinel_record_numbers_are_synthetic(self):
        self.assertEqual(patterns(SRC, "MRN: 99947101"), [])
        self.assertEqual(patterns(SRC, "FIN 000999471001"), [])

    def test_agentchecks_is_a_fixture_path(self):
        self.assertEqual(patterns("Sources/AgentChecks/Cases.swift", "MRN: " + EIGHT), [])


class G5_ImpossibleNanpNumbersAreSynthetic(unittest.TestCase):
    def test_area_code_555_is_synthetic_in_every_format(self):
        self.assertEqual(patterns(SRC, "(555) 234-5678"), [])
        self.assertEqual(patterns(SRC, "555-234-5678"), [])
        self.assertEqual(patterns(SRC, "555.234.5678"), [])
        self.assertEqual(patterns(SRC, "+1 555 234 5678"), [])
        self.assertEqual(patterns(SRC, "phone: 5552345678"), [])

    def test_area_or_exchange_starting_0_or_1_is_synthetic(self):
        exch1 = d("1", 3) + "-" + d("4", 4)                                   # exchange starting 1: unassignable
        self.assertEqual(patterns(SRC, "(" + d("2", 3) + ") " + exch1), [])
        self.assertEqual(patterns(SRC, d("2", 3) + "-" + exch1), [])
        self.assertEqual(patterns(SRC, d("0", 3) + "-" + d("0", 3) + "-" + d("0", 4)), [])
        self.assertEqual(patterns(SRC, "(" + d("1", 3) + ") " + d("4", 3) + "-" + d("7", 4)), [])   # area starting 1

    def test_a_possible_number_is_still_flagged(self):
        self.assertEqual(patterns(SRC, "(" + d("2", 3) + ") " + d("3", 3) + "-" + d("4", 4)), ["phone"])
        self.assertEqual(patterns(SRC, "+1 " + DASHED), ["phone"])


class PracticeNumbers(unittest.TestCase):
    """The practice's own published phone and fax are business values, exempt in every format; their neighbours are not.
    The numbers come from the canonical script (never typed here), so GUARD=<pre-fix copy> fails these rows."""
    NUMBERS = sorted(load_guard_from(os.path.join(HERE, "..", "identifier-guard.py")).PRACTICE_NUMBERS)

    def formats(self, n):
        a, e, l = n[:3], n[3:6], n[6:]
        return ["call %s-%s-%s" % (a, e, l), "call %s.%s.%s" % (a, e, l), "call (%s) %s-%s" % (a, e, l),
                "call +1%s" % n, "phone: %s" % n, "call %s %s %s" % (a, e, l)]

    def test_practice_numbers_are_exempt_in_every_format(self):
        self.assertEqual(len(self.NUMBERS), 2)
        for n in self.NUMBERS:
            for line in self.formats(n):
                self.assertEqual(patterns(SRC, line), [], line.split()[0])

    def test_a_neighbouring_number_is_still_flagged(self):
        for n in self.NUMBERS:
            other = n[:-1] + str((int(n[-1]) + 1) % 10)
            for line in self.formats(other):
                self.assertEqual(patterns(SRC, line), ["phone"])


class RegulatorNumbers(PracticeNumbers):
    """The regulator's published toll-free number (quoted in consent text) is a second named business set, exempt the
    same way. Read from the canonical script, never typed here."""
    NUMBERS = sorted(getattr(load_guard_from(os.path.join(HERE, "..", "identifier-guard.py")), "REGULATOR_NUMBERS", ()))

    def test_practice_numbers_are_exempt_in_every_format(self):
        self.assertEqual(len(self.NUMBERS), 1)
        for n in self.NUMBERS:
            for line in self.formats(n):
                self.assertEqual(patterns(SRC, line), [], line.split()[0])

    def test_the_two_sets_are_separate(self):
        canonical = load_guard_from(os.path.join(HERE, "..", "identifier-guard.py"))
        self.assertFalse(set(self.NUMBERS) & set(canonical.PRACTICE_NUMBERS))


# A capitalised LAST, FIRST shape assembled at run time (never a real name), and digit strings of the code shapes.
NAME = d("K", 4) + ", " + d("J", 3)
CPT = d("2", 5)                                              # a 5-digit procedure / test code
LOINC = d("2", 5) + "-" + d("3", 1)                          # 12345-6 shape


class NameNextToACode(unittest.TestCase):
    """All-caps test names and constants next to a CPT / Quest test code or a LOINC code are not patients
    (the five S3 false positives in ccmehr and three in vcmc-visit-notes, with the names swapped for synthetic ones)."""

    def test_report_lines_are_not_flagged(self):
        for line in ("| %s | %s | — | genetic | (code-only) | |" % (NAME, CPT),
                     "| %s | 2878.76 | %s |" % (NAME, CPT),
                     "  { testName: '%s', questTestCode: '%s', patientPriceCents: 287876 }," % (NAME, CPT),
                     "    await expect(f.client.bindMedTracker(%s, { ...identity, subscriptionId: '%s' }))" % (NAME, CPT),
                     "    expect((await f.client.getById(%s))?.medTrackerBinding?.subscriptionId).toBe('%s');" % (NAME, CPT),
                     "    const s = aggregateReports(rows, %s); // no categoryMap — %s pinned inpatient" % (NAME, CPT),
                     "    const s = aggregateReports(rows, %s, { '%s': 'or' });" % (NAME, CPT),
                     "    for (const secret of [%s, 'Test Patient', '1900-01-01', '%s', 'N20.0', etag]) {" % (NAME, CPT)):
            self.assertEqual(patterns(SRC, line), [], line)

    def test_loinc_code_is_not_flagged(self):
        self.assertEqual(patterns(SRC, "| %s | 8396 | %s | single | High |" % (NAME, LOINC)), [])

    def test_a_record_number_or_date_next_to_a_name_is_still_flagged(self):
        self.assertEqual(patterns(SRC, "%s acct %s" % (NAME, d("2", 8))), ["name_near_id"])
        self.assertEqual(patterns(SRC, "%s %s" % (NAME, d("2", 6))), ["name_near_id"])
        self.assertEqual(patterns(SRC, "%s seen 03/14/%d" % (NAME, ADULT)), ["name_near_id"])

    def test_zip_shapes_next_to_a_name_are_still_flagged(self):
        self.assertEqual(patterns(SRC, "%s, 12 Main St, Ventura, CA %s" % (NAME, CPT)), ["name_near_id"])
        self.assertEqual(patterns(SRC, "%s %s-%s" % (NAME, CPT, d("3", 4))), ["name_near_id"])   # ZIP+4
        self.assertEqual(patterns(SRC, "%s %s.%s" % (NAME, CPT, d("3", 2))), ["name_near_id"])   # not a code: a decimal


class FailureMessage(unittest.TestCase):
    def test_message_names_the_allowed_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "notes.md")
            with open(p, "w") as f:
                f.write("MRN: " + EIGHT + "\n")
            r = subprocess.run([sys.executable, GUARD, "--files", p], capture_output=True, text=True)
        for shape in ("ROE/DOE/POE", "555-01xx", "area code 555", "4711", "CCM-471NN", "999", "1900-01-01", "1970-01-01", "example.com"):
            self.assertIn(shape, r.stdout, "failure message should name the allowed shape %r" % shape)


if __name__ == "__main__":
    unittest.main()
