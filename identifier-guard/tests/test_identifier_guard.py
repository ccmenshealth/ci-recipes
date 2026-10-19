#!/usr/bin/env python3
"""Tests for identifier-guard.py (stdlib unittest; python3 -m unittest discover -s identifier-guard/tests).

One test class per detection rule. Every literal in this file is one of the
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

    def test_numbers_are_exempt_in_every_format(self):
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

    def test_numbers_are_exempt_in_every_format(self):
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
    """All-caps test names and constants next to a CPT / test code or a LOINC code are not patient identifiers."""

    def test_report_lines_are_not_flagged(self):
        for line in ("| %s | %s | — | genetic | (code-only) | |" % (NAME, CPT),
                     "| %s | 100.00 | %s |" % (NAME, CPT),
                     "  { testName: '%s', questTestCode: '%s', patientPriceCents: 10000 }," % (NAME, CPT),
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
        self.assertEqual(patterns(SRC, "%s 0.%s" % (NAME, CPT)), ["name_near_id"])               # nor a 5-digit run inside one

    def test_a_record_number_or_date_after_a_code_is_still_flagged(self):
        self.assertEqual(patterns(SRC, "%s %s seen 03/14/%d" % (NAME, CPT, ADULT)), ["name_near_id"])
        self.assertEqual(patterns(SRC, "%s %s then %s" % (NAME, CPT, d("2", 9))), ["name_near_id"])
        self.assertEqual(patterns(SRC, "%s %s acct %s" % (NAME, LOINC, d("2", 8))), ["name_near_id"])
        self.assertEqual(patterns(SRC, "%s %s acct-%s" % (NAME, CPT, d("2", 8))), ["name_near_id"])
        self.assertEqual(patterns(SRC, "%s %s dos %d-03-14" % (NAME, CPT, ADULT)), ["name_near_id"])
        self.assertEqual(patterns(SRC, "%s %s dos 03-14-%d" % (NAME, CPT, ADULT)), ["name_near_id"])


PERSONAL_EMAIL = "dev" + "@" + "gmail.com"                   # a personal-domain address, assembled at run time


class LockfilesAreSkipped(unittest.TestCase):
    """Machine-written dependency lockfiles (package authors' e-mails, hashes) are never our data."""
    LOCKFILES = ("composer.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "Package.resolved",
                 "web/package-lock.json", "App.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved")

    def test_lockfiles_are_not_scanned(self):
        for path in self.LOCKFILES:
            self.assertEqual(patterns(path, '"email": "%s"' % PERSONAL_EMAIL), [], path)

    def test_other_files_and_lookalike_names_are_still_scanned(self):
        for path in ("composer.json", "package.json", "composer.lock.md", "notes/yarn.lock.txt", "my-package-lock.json"):
            self.assertEqual(patterns(path, '"email": "%s"' % PERSONAL_EMAIL), ["email"], path)

    def test_whole_file_mode_skips_a_lockfile(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "composer.lock")
            with open(p, "w") as f:
                f.write('"email": "%s"\n' % PERSONAL_EMAIL)
            r = subprocess.run([sys.executable, GUARD, "--files", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


class FailureMessage(unittest.TestCase):
    def test_message_names_the_allowed_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "notes.md")
            with open(p, "w") as f:
                f.write("MRN: " + EIGHT + "\n")
            r = subprocess.run([sys.executable, GUARD, "--files", p], capture_output=True, text=True)
        for shape in ("ROE/DOE/POE", "555-01xx", "area code 555", "4711", "CCM-471NN", "999", "1900-01-01", "1970-01-01", "example.com"):
            self.assertIn(shape, r.stdout, "failure message should name the allowed shape %r" % shape)

# --- person-data rules (v1.8). Flagged lines are assembled at run time so this file never holds one: the first name is
# taken from the canonical script's own shape list, the surname is an invented string, amounts are round. ---
CANONICAL = load_guard_from(os.path.join(HERE, "..", "identifier-guard.py"))
FIRST = sorted(CANONICAL.GIVEN_NAMES)[0]                    # a list entry, never typed here
SURNAME = "Zq" + "xwick"                                    # invented
FULL = FIRST + " " + SURNAME
AMT = "$" + "1,000.00"
PAY = "p" + "ay"
FOUR = d("2", 4)
TEST = "tests/unit/widget.test.ts"                          # a fixture path
DOC = "docs/runbooks/widget.md"                             # a doc file


def opt_in_patterns(path, text, *rules):
    """Pattern names reported with the named opt-in rules switched on (the default set always runs)."""
    findings = []
    guard.check_line(path, 1, text, findings, frozenset(rules))
    return sorted(k for _, _, k in findings)


class NameNearAmount(unittest.TestCase):
    """A First Last name (first word from the given-name shape list) near money or pay words, in any path."""

    def test_name_next_to_money_is_flagged(self):
        for line in (FULL + " paid " + AMT, "invoice for " + FULL + " sent", FULL + " owes " + AMT,
                     FULL + ": payroll run", FIRST + " Q. " + SURNAME + " " + AMT, FULL + " acct " + d("2", 6)):
            self.assertEqual(patterns(SRC, line), ["name_amount"], line.replace(FIRST, "<first>"))

    def test_given_name_alone_before_or_after_a_dollar_amount_is_flagged(self):
        self.assertEqual(patterns(SRC, FIRST + ": " + AMT), ["name_amount"])
        self.assertEqual(patterns(SRC, AMT + " to " + FIRST), ["name_amount"])

    def test_fixture_paths_and_docs_are_checked_too(self):
        self.assertEqual(patterns(TEST, "{ name: '" + FULL + "', amount: '" + AMT + "' }"), ["name_amount"])
        self.assertEqual(patterns(DOC, "| " + FULL + " | " + AMT + " |"), ["name_amount"])

    def test_persona_surname_is_exempt(self):
        for persona in ("Roe", "Doe", "Poe", "Bloggs", "Mockley", "Stubbs", "Fakeman"):
            self.assertEqual(patterns(SRC, FIRST + " " + persona + " paid " + AMT), [], persona)

    def test_title_case_prose_code_names_and_dates_are_not_flagged(self):
        for line in ("Monthly Close paid " + AMT, "Testosterone Total " + d("2", 5), "const " + FIRST + SURNAME + " = 1000",
                     FULL + " seen %d-03-14" % ADULT, FULL + " on March 14, %d" % ADULT, FULL + " reviewed the plan",
                     FIRST + " Payment " + AMT, "100 Main St, San " + FIRST + " Zqxton, CA " + d("2", 5)):
            self.assertEqual(patterns(SRC, line), [], line.replace(FIRST, "<first>"))

    def test_marker_word_still_exempts_the_line(self):
        self.assertEqual(patterns(SRC, FULL + " paid " + AMT + "  # synthetic"), [])

    def test_amounts_that_look_like_years_are_still_money(self):
        for amount in ("$" + "2000", "$" + "1999.00", "$" + "2050.00", "2000" + ".00"):
            self.assertEqual(patterns(SRC, FULL + " " + amount), ["name_amount"], amount)

    def test_a_date_before_the_name_is_not_money(self):
        self.assertEqual(patterns(SRC, "on March 14, %d " % ADULT + FULL + " reviewed it"), [])

    def test_long_lines_stay_linear(self):
        import time
        line = (FIRST + " " + FIRST + " ") * (60000 // (2 * len(FIRST) + 2))   # diff mode has no line-length cap
        start = time.monotonic()
        patterns(SRC, line)
        self.assertLess(time.monotonic() - start, 3.0)

    def test_shape_list_is_ascii_sorted_and_names_only(self):
        names = CANONICAL.GIVEN_NAMES_SOURCE.split()
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(n.isascii() and n.isalpha() and n[0].isupper() for n in names))
        self.assertGreater(len(names), 400)


class PayNearPerson(unittest.TestCase):
    """Pay tied to a person: his/her pay near an amount, she/he takes home an amount, Name's pay near an amount."""

    def test_pronoun_pay_is_flagged(self):
        for line in ("her gross " + PAY + " was " + AMT, AMT + " is his sal" + "ary", "she takes ho" + "me ~" + AMT,
                     "her wa" + "ges came to " + AMT):
            self.assertEqual(patterns(SRC, line), ["pay_near_person"], line)

    def test_possessive_pay_is_flagged(self):
        self.assertEqual(patterns(SRC, SURNAME + "'s net " + PAY + ": " + AMT), ["pay_near_person"])
        self.assertEqual(patterns(DOC, SURNAME + "&apos;s gross " + PAY + " of " + AMT), ["pay_near_person"])
        self.assertEqual(patterns(SRC, FIRST + "'s net " + PAY + ": " + AMT), ["name_amount", "pay_near_person"])

    def test_pay_without_a_person_is_not_flagged(self):
        for line in ("net " + PAY + ": " + AMT, "the employee's " + PAY + " is " + AMT, "Gusto's net " + PAY + " " + AMT,
                     "her " + PAY + " stub is attached"):
            self.assertEqual(patterns(SRC, line), [], line)


class PartialId(unittest.TestCase):
    """Last-4 fragments of real ids in docs and source; fixture paths only when opted in (fixture_partial_id)."""

    def test_labelled_or_masked_last_four_is_flagged(self):
        for line in ("card ending in " + FOUR, "last 4: " + FOUR, "last-four digits " + FOUR, "ends with " + FOUR,
                     "****" + FOUR, "xxxx" + FOUR, "•••-" + FOUR, "•••• " + FOUR, "**** **** **** " + FOUR,
                     "XXXX-XXXX-XXXX-" + FOUR):
            self.assertEqual(patterns(SRC, line), ["partial_id"], line)
            self.assertEqual(patterns(DOC, line), ["partial_id"], line)

    def test_allowed_endings_dates_and_longer_numbers_are_not(self):
        for line in ("ending in 4711", "last4: '0000'", "last 4: 4242", "ending 1111", "ending %d-03-14" % ADULT,
                     "last 4 of " + d("2", 5), "/** " + FOUR + " note */", "**" + FOUR + "**", "ending " + FOUR + ".56"):
            self.assertEqual(patterns(SRC, line), [], line)

    def test_fixture_paths_only_when_opted_in(self):
        line = "{ last4: '" + FOUR + "' }"
        self.assertEqual(patterns(TEST, line), [])
        self.assertEqual(opt_in_patterns(TEST, line, "fixture_partial_id"), ["fixture_partial_id"])
        self.assertEqual(opt_in_patterns(TEST, "{ last4: '4711' }", "fixture_partial_id"), [])


class MockupScope(unittest.TestCase):
    """Mockups get the fixture-shaped opt-in rules (names, addresses, e-mail) but are checked like source for DOBs and
    last-4 fragments, so one value is never reported twice."""
    MOCKUP = "docs/mockups/home.html"

    def test_no_double_report_in_mockups(self):
        everything = tuple(guard.OPT_IN_RULES)
        self.assertEqual(opt_in_patterns(self.MOCKUP, "dob: '%d-03-14'" % ADULT, *everything), ["dob"])
        self.assertEqual(opt_in_patterns(self.MOCKUP, "card ending in " + FOUR, *everything), ["partial_id"])

    def test_email_domain_covers_mockups(self):
        self.assertEqual(opt_in_patterns("mockups/a.css", "ops" + "@" + "zqx-" + "clinic.com", "email_domain"),
                         ["email_domain"])


class OptInFixtureName(unittest.TestCase):
    def test_off_by_default_and_flagged_when_opted_in(self):
        line = "  lastName: '" + SURNAME + "',"
        self.assertEqual(patterns(TEST, line), [])
        self.assertEqual(opt_in_patterns(TEST, line, "fixture_name"), ["fixture_name"])
        self.assertEqual(opt_in_patterns("docs/mockups/home.html", "patientName: '" + SURNAME + "'", "fixture_name"),
                         ["fixture_name"])

    def test_persona_values_empty_and_templates_pass(self):
        for line in ("lastName: 'Roe'", "patient_name: 'Jane Doe'", "fullName: \"BLOGGS, J\"", "lastName: ''",
                     "lastName: '${last}'", "surname: '{{ surname }}'"):
            self.assertEqual(opt_in_patterns(TEST, line, "fixture_name"), [], line)

    def test_source_paths_are_not_in_scope(self):
        self.assertEqual(opt_in_patterns(SRC, "lastName: '" + SURNAME + "'", "fixture_name"), [])


class OptInFixtureDob(unittest.TestCase):
    def test_off_by_default_and_flagged_when_opted_in(self):
        line = "dob: '%d-03-14'" % ADULT
        self.assertEqual(patterns(TEST, line), [])
        self.assertEqual(opt_in_patterns(TEST, line, "fixture_dob"), ["fixture_dob"])

    def test_placeholders_pass(self):
        for line in ("dob: '1970-01-01'", "dob: '1900-01-01'", "dob: '%d-03-14'" % (THIS_YEAR + 1)):
            self.assertEqual(opt_in_patterns(TEST, line, "fixture_dob"), [], line)


UUID = "-".join([d("2", 4) + d("a", 4), d("3", 4), d("4", 4), d("8", 4), d("b", 6) + d("c", 6)])


class OptInDocUuid(unittest.TestCase):
    def test_off_by_default_and_flagged_in_docs_when_opted_in(self):
        line = "operation " + UUID + " finished"
        self.assertEqual(patterns(DOC, line), [])
        self.assertEqual(opt_in_patterns(DOC, line, "doc_uuid"), ["doc_uuid"])
        self.assertEqual(opt_in_patterns("NOTES.md", line, "doc_uuid"), ["doc_uuid"])

    def test_code_and_nil_uuids_pass(self):
        self.assertEqual(opt_in_patterns(SRC, "const id = '" + UUID + "'", "doc_uuid"), [])
        self.assertEqual(opt_in_patterns(DOC, "00000000-0000-0000-0000-000000000000", "doc_uuid"), [])


class OptInFixtureAddress(unittest.TestCase):
    def test_off_by_default_and_flagged_when_opted_in(self):
        for line in ("Zqxton, CA " + d("6", 5), "Zqx Falls, CA " + d("6", 5) + "-" + d("7", 4), "zip: '" + d("6", 5) + "'",
                     '"postalCode": "' + d("6", 5) + '"'):
            self.assertEqual(patterns(TEST, line), [], line)
            self.assertEqual(opt_in_patterns(TEST, line, "fixture_address"), ["fixture_address"], line)

    def test_allowed_zips_and_non_address_words_pass(self):
        for line in ("zip: '00012'", "Zqxton, CA 47110", "zip: '99999'", "Use " + d("6", 5), "port " + d("6", 5)):
            self.assertEqual(opt_in_patterns(TEST, line, "fixture_address"), [], line)


class OptInEmailDomain(unittest.TestCase):
    ADDR = "ops" + "@" + "zqx-" + "clinic.com"            # invented; assembled so no literal address sits here

    def test_off_by_default_and_flagged_in_tests_and_docs_when_opted_in(self):
        for path in (TEST, DOC):
            self.assertEqual(patterns(path, "to: '" + self.ADDR + "'"), [])
            self.assertEqual(opt_in_patterns(path, "to: '" + self.ADDR + "'", "email_domain"), ["email_domain"])

    def test_reserved_domains_versions_and_source_pass(self):
        for line in ("a@example.com", "b@example.org", "c@example.net", "d@host.test", "e@mail.example", "noreply" + "@" + "zqx" + ".com",
                     "git@github.com:org/repo", "pkg@1.2.3", "x@users.noreply.github.com"):
            self.assertEqual(opt_in_patterns(TEST, line, "email_domain"), [], line)
        self.assertEqual(opt_in_patterns(SRC, self.ADDR, "email_domain"), [])


class OptInConfig(unittest.TestCase):
    """The guard reads guard_rules from .repo-standard.json at the repo root, in every mode, so hook and CI agree."""

    def make_repo(self, td, config):
        subprocess.run(["git", "init", "-q", td], check=True)
        if config is not None:
            with open(os.path.join(td, ".repo-standard.json"), "w") as f:
                f.write(config)
        os.makedirs(os.path.join(td, "tests"))
        p = os.path.join(td, "tests", "a.test.ts")
        with open(p, "w") as f:
            f.write("dob: '%d-03-14'\n" % ADULT)
        return p

    def run_in(self, td, *args):
        return subprocess.run([sys.executable, GUARD, *args], capture_output=True, text=True, cwd=td)

    def test_rule_runs_only_when_listed(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.make_repo(td, None)
            self.assertEqual(self.run_in(td, "--files", p).returncode, 0)
        with tempfile.TemporaryDirectory() as td:
            p = self.make_repo(td, '{"version": 1, "guard_rules": ["fixture_dob"]}')
            r = self.run_in(td, "--files", p)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("a.test.ts:1 fixture_dob", r.stdout)

    def test_staged_mode_reads_the_same_key(self):
        with tempfile.TemporaryDirectory() as td:
            self.make_repo(td, '{"guard_rules": ["fixture_dob"]}')
            subprocess.run(["git", "-C", td, "add", "-A"], check=True)
            r = self.run_in(td, "--staged")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("fixture_dob", r.stdout)

    def test_base_mode_from_a_subdirectory_reads_the_root_key(self):
        with tempfile.TemporaryDirectory() as td:
            self.make_repo(td, '{"guard_rules": ["fixture_dob"]}')
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@example.com")
            subprocess.run(["git", "-C", td, "commit", "-q", "--allow-empty", "-m", "base"], check=True, env=env)
            subprocess.run(["git", "-C", td, "add", "-A"], check=True)
            subprocess.run(["git", "-C", td, "commit", "-q", "-m", "head"], check=True, env=env)
            r = self.run_in(os.path.join(td, "tests"), "--base", "HEAD~1", "--head", "HEAD")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("fixture_dob", r.stdout)

    def test_no_git_on_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "a.md")
            with open(p, "w") as f:
                f.write("clean\n")
            r = subprocess.run([sys.executable, GUARD, "--files", p], capture_output=True, text=True, cwd=td,
                               env={"PATH": os.path.join(td, "no-such-dir")})
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_unknown_rule_bad_json_and_wrong_type_fail_closed(self):
        for config in ('{"guard_rules": ["fixture_dobb"]}', "{not json", '{"guard_rules": "fixture_dob"}'):
            with tempfile.TemporaryDirectory() as td:
                p = self.make_repo(td, config)
                r = self.run_in(td, "--files", p)
                self.assertEqual(r.returncode, 2, config + r.stdout + r.stderr)

    def test_opt_in_names_are_the_documented_set(self):
        self.assertEqual(set(guard.OPT_IN_RULES), {"fixture_name", "fixture_dob", "fixture_partial_id", "doc_uuid",
                                                   "fixture_address", "email_domain"})


if __name__ == "__main__":
    unittest.main()
