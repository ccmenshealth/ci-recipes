#!/usr/bin/env python3
"""Identifier guard: fails when ADDED lines in a diff look like patient identifiers.

Canonical copy: https://github.com/ccmenshealth/ci-recipes (identifier-guard/identifier-guard.py). Every repo carries a
byte-identical copy at scripts/identifier-guard.py so the pre-commit hook works offline; the shared workflow fails a PR
whose copy differs. Change it there and run sync.sh, never in a single repo.

Checked patterns (on added lines only): SSN (dashed anywhere; labelled with or without dashes); labelled DOB
(numeric or month-name dates) with a possible birth year; labelled MRN/FIN digits; CCM-NNNNN record numbers; US phone
numbers (dashed, dotted, spaced, parenthesised, +1, and ten contiguous digits after a phone-ish label); LAST, FIRST
(caps) next to a 5+ digit number or a date (not when that number has a 5-digit CPT / test-code or LOINC shape and no
7+ digit number or date follows); personal e-mail domains.
A line is allowed when it carries a synthetic marker (ROE/DOE/POE personas, 555-01xx or area-code-555 phones, any
impossible NANP number (area code or exchange starting 0/1), the practice's own published phone and fax, the regulator's
published toll-free number, the 4711/471
synthetic number block, CCM-471NN, the 999-prefixed sentinel record numbers, example.com, or the words synthetic/fixture/
placeholder/sentinel) or sits in a fixture path (test/fixture/mock directories, *.test.* / *.spec.* files, files named *fixtures.*, Sources/AgentChecks).
Dependency lockfiles (composer.lock, package-lock.json, yarn.lock, pnpm-lock.yaml, Cargo.lock, Package.resolved) are not
scanned: machine-written, never our data.
Labelled DOBs: 1900-01-01 and 1970-01-01 are documented placeholders; years before 1900 or after this year are
impossible; years 2020 to this year count only in value position (`DOB: 20xx-mm-dd`, `"dob": "..."`), not after prose
(so `dob: new Date("20xx-mm-dd")` is not flagged either: letters sit between label and date).
Output names file, line and pattern only; it never prints the matched text.

usage: identifier-guard.py --base <rev> --head <rev>      (scans `git diff base..head`; --three-dot for base...head)
       identifier-guard.py --staged                        (scans the index, for a pre-commit hook)
       identifier-guard.py --files f1 f2 ...               (scans whole files; skips binaries and lines over 2,000 chars;
                                                            an unreadable path is an error, exit 2)
exit 0 clean, 1 findings, 2 could not scan.
"""
import re, subprocess, sys, argparse
from datetime import date

MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE = (r"(?:\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}"                       # 03/14/1987, 1987-03-14, 14.03.1987
        r"|(?i:" + MONTH + r")[\s-]+\d{1,2},?[\s-]+\d{4}"               # March 14, 1987; Mar-14-1987
        r"|\d{1,2}[\s-]+(?i:" + MONTH + r"),?[\s-]+\d{4})")             # 14 Mar 1987; 14-Mar-1987 (Cerner)
DOB_LABEL = r"(?:(?i:(?<![a-z])(?:dob|date[\s_]*of[\s_]*birth|birth[\s_]*date|dateofbirth|birthdate))|(?<=[a-z])(?:DOB|Dob))(?![a-z])"
PHONE_LABEL = r"(?:(?i:(?<![a-z])(?:phone|tel|cell|mobile|fax|sms|number|contact|caller|e164))|(?<=[a-z])(?:Phone|Tel|Cell|Mobile|Fax|Sms|Number|Contact|Caller))"
SSN_LABEL = r"(?:(?i:(?<![a-z])(?:ssn|social[\s_]*security(?:[\s_]*(?:number|no\.?|#))?))|(?<=[a-z])Ssn)(?![a-z])"
SSN_DIGITS = r"(?!000|666|9\d\d)(\d{3})%s(?!00)(\d{2})%s(?!0000)(\d{4})"
PATTERNS = {   # name -> list of regexes; each match is judged on its own text
    "ssn": [re.compile(r"\b" + SSN_DIGITS % ("-", "-") + r"\b"),
            re.compile(SSN_LABEL + r"[^\n\d]{0,15}\b" + SSN_DIGITS % ("[ -]?", "[ -]?") + r"\b")],
    "dob": [re.compile(DOB_LABEL + r"([^\n\d]{0,25})(" + DATE + r")")],
    "mrn": [re.compile(r"(?:(?i:(?<![a-z])(?:mrn|fin))|(?<=[a-z])(?:Mrn|Fin))(?![a-z])[^\n\d]{0,15}(\d{5,14})\b")],
    "ccm": [re.compile(r"(?i)\bCCM[-\s]?\d{5}\b")],
    "phone": [re.compile(r"(?<![\d.])(?:\(\d{3}\)\s?\d{3}[-. ]\d{4}|\b\d{3}([-.])\d{3}\1\d{4}|\b[2-9]\d{2} [2-9]\d{2} \d{4}"
                         r"|\+1[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{4}|\+1\d{10})\b(?![\d.])"),
              re.compile(PHONE_LABEL + r"[^\n]{0,40}?(?<![\d.])(?P<num>[2-9]\d{2}[2-9]\d{6})(?![\d.])")],   # ten contiguous digits, label within 40 chars
    "name_near_id": [re.compile(r"\b[A-Z][A-Z'\-]{2,30}(?:\s[A-Z][A-Z'\-]{1,30})?,\s?[A-Z][A-Z'\-]{1,30}\b[^\n]{0,80}?(?:\b(?P<id>\d{5,14})\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b)")],
    "email": [re.compile(r"(?i)(?<![a-z0-9._%+\-])[a-z0-9._%+\-]{1,64}@(?:gmail|googlemail|yahoo|ymail|rocketmail|hotmail|outlook|icloud|mac|me|aol|live|msn|comcast|att|sbcglobal|verizon|cox|charter|earthlink|frontier|proton|protonmail|pm|gmx)\.(?:com|net|me|co\.uk)\b")],
}
LINE_MARKER = re.compile(r"(?i)\b(?:synthetic|fixture|placeholder|sentinel)\b")   # explicit, documented opt-out words only
SYNTH_VALUE = re.compile(r"\b(?:ROE|DOE|POE|BLOGGS|MOCKLEY|STUBBS|FAKEMAN)\b|555[-. ]?01\d\d|55501\d\d|\b4711\d*\b|\b0*999\d{5,7}\b|(?i:CCM[-\s]?)?\b471\d{2}\b|(?i:CCM[-\s]?)(?:0000\d|12345|99999)\b")
PLACEHOLDER_DOB = re.compile(r"(?i)^(?:(?:1900|1970)[-/.]0?1[-/.]0?1|0?1[-/.]0?1[-/.](?:1900|1970)|jan[a-z]*\.?[\s-]+0?1,?[\s-]+(?:1900|1970)|0?1[\s-]+jan[a-z]*\.?,?[\s-]+(?:1900|1970))$")
LOCKFILE = re.compile(r"(^|/)(?:composer\.lock|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|Package\.resolved)$")   # machine-written, never our data
FIXTURE_PATH = re.compile(r"(?i)(^|/)(tests?|__tests__|fixtures?|__fixtures__|__mocks__|mocks?|e2e|replay|worlds?|AgentChecks)(/|$)|\.(test|spec)\.[a-z]+$|(^|/)[^/]*fixtures?\.[^/]+$")
RECENT_BIRTH_YEAR = 2020     # 2020..this year: a child's DOB; flagged only in value position (no words between label and date)
MAX_LINE = 2000              # --files mode: longer lines are minified bundles, not prose
ALLOWED_SHAPES = ("Use the allowed test-data shapes: ROE/DOE/POE personas, 555-01xx or area code 555 phones, 4711… numbers, "
                  "CCM-471NN, 999-prefixed sentinel record numbers, 1900-01-01 / 1970-01-01 placeholder dates, example.com e-mail; "
                  "or move the data out of the repo.")


# The practice's own published phone and fax (letterhead, website, fax cover, forms): business numbers, not patient data.
# Exact numbers only; any other number, including a neighbour of these, is still checked. Never add a person's number here.
PRACTICE_NUMBERS = frozenset({"8053928700", "8554204671"})
# The regulator's published toll-free complaint number, quoted in the surgery consent text: a business number, exempt the
# same way (exact match only). A separate set so each exemption says what it is.
REGULATOR_NUMBERS = frozenset({"8009853059"})


def impossible_nanp(digits):
    """True for a 10-digit US number that cannot be assigned: area code 555, or area code / exchange starting 0 or 1."""
    return digits[:3] == "555" or digits[0] in "01" or digits[3] in "01"


def code_number(m):
    """True when the first number after a LAST, FIRST shape has the code shape, not a record number's: any standalone
    5-digit number (CPT, Quest test code) or a LOINC code (12345-6). Not a code: a 5-digit run inside a longer number
    (12345.67, 0.12345, CCM-12345), ZIP+4, or five digits right after an upper-case 2-letter state abbreviation. Even
    after a code, a 7+ digit number or a date in the next 80 characters still flags the line (name, CPT, date, account)."""
    if not m.group("id") or len(m.group("id")) != 5:
        return False
    before, after = m.string[:m.start("id")], m.string[m.end("id"):]
    if re.search(r"[-.]$", before) or re.search(r"\b[A-Z]{2},?\s+$", before):
        return False
    if re.search(r"(?<![\d.])\d{7,14}(?!\d)|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{1,2}-\d{1,2}\b", after[:80]):
        return False
    return re.match(r"-\d(?!\d)", after) is not None or re.match(r"[-.]\d", after) is None


def synthetic(name, m):
    text = m.groupdict().get("num") or m.group(0)      # a labelled number is judged on the number, not on the label or the gap
    if SYNTH_VALUE.search(text):
        return True
    if name == "phone":
        digits = re.sub(r"\D", "", text)
        digits = digits[-10:] if len(digits) >= 10 else digits
        return len(digits) == 10 and (impossible_nanp(digits) or digits in PRACTICE_NUMBERS or digits in REGULATOR_NUMBERS)
    if name == "name_near_id":
        return code_number(m)
    if name == "dob":
        gap, value = m.group(1), m.group(2)
        if PLACEHOLDER_DOB.match(value):
            return True
        years = re.findall(r"(?<!\d)\d{4}(?!\d)", value)
        if not years:
            return False                          # two-digit year: cannot prove it impossible
        year = int(years[0])
        if year < 1900 or year > date.today().year:
            return True
        return year >= RECENT_BIRTH_YEAR and re.search(r"[A-Za-z]", gap) is not None
    return False


def check_line(path, lineno, text, findings):
    if LOCKFILE.search(path) or LINE_MARKER.search(text):
        return
    fixture = bool(FIXTURE_PATH.search(path))
    for name, rxs in PATTERNS.items():
        if fixture and name in ("ccm", "mrn", "dob"):
            continue   # test fixtures may carry record-number/DOB shapes; names, phones, e-mails and SSNs are still checked there
        if any(not synthetic(name, m) for rx in rxs for m in rx.finditer(text)):   # every match judged on its OWN text, not the line
            findings.append((path, lineno, name))


GIT_DIFF = ["git", "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--unified=0"]


def scan_diff(args):
    r = subprocess.run(GIT_DIFF + args, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:                      # fail CLOSED: an unreadable diff is never "clean"
        print(f"identifier guard: git diff failed (exit {r.returncode}); refusing to pass")
        sys.exit(2)
    findings, path, lineno, in_hunk = [], None, 0, False
    for line in r.stdout.splitlines():
        if line.startswith("diff --git "):
            in_hunk, path = False, None
        elif not in_hunk and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("@@"):
            in_hunk = True
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) - 1 if m else 0
        elif in_hunk and line.startswith("+"):
            lineno += 1
            if path and path != "/dev/null":
                check_line(path, lineno, line[1:], findings)
    return findings


def scan_files(paths):
    findings = []
    for p in paths:
        try:
            with open(p, "rb") as f:
                if b"\0" in f.read(8192):
                    continue                   # binary (PDF, font, image): not prose
            with open(p, encoding="utf-8", errors="replace") as f:
                for i, text in enumerate(f, 1):
                    if len(text) <= MAX_LINE:
                        check_line(p, i, text, findings)
        except OSError as e:                   # fail CLOSED: an unreadable file is never "clean"
            print(f"identifier guard: cannot read {p}: {e.strerror}; refusing to pass")
            sys.exit(2)
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base"); ap.add_argument("--head", default="HEAD"); ap.add_argument("--staged", action="store_true")
    ap.add_argument("--files", nargs="*"); ap.add_argument("--three-dot", action="store_true")
    a = ap.parse_args()
    if a.files:
        findings = scan_files(a.files)
    elif a.staged:
        findings = scan_diff(["--cached"])
    else:
        if not a.base:
            sys.exit("need --base <rev> (or --staged / --files)")
        findings = scan_diff([f"{a.base}...{a.head}" if a.three_dot else f"{a.base}..{a.head}", "--"])
    if findings:
        print("identifier guard: possible patient identifiers in added lines (file:line pattern). " + ALLOWED_SHAPES
              + " If this is a push to the default branch, the text is already in history: tell Steve before doing anything else.")
        for p, n, k in findings:
            print(f"  {p}:{n} {k}")
        sys.exit(1)
    print("identifier guard: clean")


if __name__ == "__main__":
    main()
