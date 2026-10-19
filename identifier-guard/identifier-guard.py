#!/usr/bin/env python3
"""Identifier guard: fails when ADDED lines in a diff look like patient identifiers.

Canonical copy: identifier-guard/identifier-guard.py in ci-recipes (the same bytes in every copy of ci-recipes). Every repo carries a
byte-identical copy at scripts/identifier-guard.py so the pre-commit hook works offline; the shared workflow fails a PR
whose copy differs. Change it there and run sync.sh, never in a single repo.

Checked patterns (on added lines only): SSN (dashed anywhere; labelled with or without dashes); labelled DOB
(numeric or month-name dates) with a possible birth year; labelled MRN/FIN digits; CCM-NNNNN record numbers; US phone
numbers (dashed, dotted, spaced, parenthesised, +1, and ten contiguous digits after a phone-ish label); LAST, FIRST
(caps) next to a 5+ digit number or a date (not when that number has a 5-digit CPT / test-code or LOINC shape and no
7+ digit number or date follows); personal e-mail domains; person data: a First Last name (first word from the
given-name shape list) or a lone given name near money or pay words (name_amount), his/her pay or Name's pay near a dollar
amount (pay_near_person), a labelled or masked last-4 fragment outside fixture paths (partial_id).
Opt-in rules, switched on per repo by "guard_rules" in .repo-standard.json at the repo root (read in every mode, so the
hook and CI agree): fixture_name, fixture_dob, fixture_partial_id, fixture_address (fixture and mockup paths),
doc_uuid (doc files), email_domain (fixture, mockup and doc paths). An unknown rule name or a bad file is exit 2.
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
import re, subprocess, sys, argparse, json, os
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


# --- person-data rules ---
# A SHAPE list, not a list of people: common US given names, used only to tell a person-name shape ("First Last") from
# Title-Case prose and headings. Given names that are also ordinary English words (Will, May, Mark, Grant, Jack…) are left
# out on purpose. ASCII-sorted, one space between names.
GIVEN_NAMES_SOURCE = """
Aaliyah Aaron Abigail Abraham Adam Adeline Adrian Adriana Adrienne Agnes Aileen Alan Alana Albert Alberto
Alejandra Alejandro Alexander Alexandra Alexandria Alexis Alfonso Alfredo Alice Alicia Alison Allan Allen
Allison Alvin Alyssa Amanda Amelia Amy Ana Andre Andrea Andres Andrew Angela Angelica Angelina Angelo Anita Anna
Annette Anthony Antoinette Antonio Aria Ariana Arlene Armando Arnold Arthur Arturo Ashley Audra Audrey Barbara
Beatrice Beatriz Belinda Benito Benjamin Bernadette Bernard Bernardo Bethany Betty Beverly Bianca Bonnie Bradley
Brandon Brenda Brent Brian Briana Brianna Bridget Brittany Brittney Bryan Byron Caleb Calvin Cameron Camila
Candace Carissa Carla Carlos Carmela Caroline Carolyn Cassandra Catherine Cecilia Celeste Cesar Charlene Charles
Cheryl Cheyenne Chloe Christa Christina Christine Christopher Cindy Clara Clarence Claudia Clifford Cody Colette
Colin Colleen Connie Connor Corinne Cornelius Curtis Cynthia Daniel Daniela Danielle Darius Darlene Darnell
Darren Darryl David Deanna Deborah Debra Delia Delores Denise Dennis Derek Desiree Diana Diane Diego Dolores
Dominic Dominique Donald Donna Donovan Doris Dorothy Douglas Dwight Edgar Edith Edna Eduardo Edward Edwin Eileen
Elaine Eleanor Elena Elias Elijah Elisa Elise Elizabeth Ellen Elliott Eloise Emilia Emilio Emily Emma Emmanuel
Enrique Eric Erica Erick Erik Estela Esther Ethan Eugene Eugenia Eunice Evan Evangeline Evelyn Everett Felicia
Fernanda Fernando Fiona Floyd Francis Francisco Franklin Gabriel Gabriela Gabriella Gabrielle Garrett Genevieve
George Gerald Geraldine Gerardo Gertrude Gilbert Gina Giselle Gregory Guadalupe Gustavo Gwendolyn Haley Hannah
Harold Harvey Hector Helen Henry Herbert Herman Hilda Horace Howard Ian Ignacio Ingrid Irene Isaac Isabel
Isabella Isabelle Isaiah Ivan Jacob Jacqueline Jacquelyn Jaime James Jane Janelle Janet Janice Janine Jared
Jasmine Jason Jasper Javier Jeanette Jeanne Jeffery Jeffrey Jennifer Jeremiah Jeremy Jerome Jerry Jesse Jessica
Jesus Jill Jillian Joanne Joaquin Jocelyn Joel Johanna John Jonah Jonathan Jorge Jose Josefina Joseph Josephine
Joshua Joyce Juan Juanita Judith Julia Julian Julianne Julie Julio Justin Justine Karen Karina Katelyn Katherine
Kathleen Kathryn Katrina Kayla Keith Kendra Kenneth Kevin Kimberly Kristen Kristin Kristina Kristy Kurt Kyle
Kylie Larissa Larry Latoya Laura Laurie Lawrence Layla Leila Leo Leon Leona Leonard Leroy Leslie Leticia Liam
Liliana Lillian Linda Lionel Lisa Lloyd Lois Lorena Lorenzo Lorraine Louis Louise Lowell Lucas Lucia Lucille
Luis Luisa Luke Luther Lydia Mackenzie Malcolm Manuel Marcela Marcia Marco Marcus Margaret Margarita Maria
Maribel Marilyn Mario Marisa Marissa Marjorie Marlene Marta Martha Martin Marvin Mary Mateo Matthew Maureen
Maurice Maxine Maxwell Megan Meghan Melanie Melissa Melvin Meredith Micah Michael Michelle Miguel Mildred
Miranda Miriam Mitchell Monica Monique Morris Nadia Nancy Naomi Natalia Natalie Natasha Nathan Nathaniel Neil
Nicholas Nicole Nina Noah Noelle Nolan Norma Norman Oliver Olivia Omar Orlando Oscar Oswaldo Owen Pablo Pamela
Patricia Patrick Paul Paula Paulina Pedro Peggy Peter Philip Phillip Phyllis Priscilla Quentin Rachel Rafael
Ramon Randall Raquel Raul Raymond Rebecca Rebekah Reginald Renee Rhonda Ricardo Richard Ricky Rita Robert
Roberta Roberto Rochelle Roger Roland Ronald Rosalind Rosemary Roxanne Ruben Russell Ryan Sabrina Salvador
Samantha Samuel Sandra Santiago Sara Sarah Saul Scarlett Scott Sean Sebastian Selena Serena Sergio Seth Sharon
Shawn Sheila Sheldon Sheryl Shirley Sidney Silvia Simone Sofia Sonia Sonya Sophia Stacey Stacy Stanley Stephanie
Stephen Steve Steven Stuart Susan Susana Sylvia Tabitha Tamara Tammy Tanya Teresa Teri Terrence Thelma Theodore
Theresa Thomas Tiffany Timothy Tobias Travis Trevor Tristan Ulysses Valeria Valerie Vanessa Vernon Veronica
Vicki Victor Vincent Vivian Viviana Wallace Walter Wanda Wesley Whitney Wilbur Wilma Xavier Yesenia Yolanda
Yvette Yvonne Zachariah Zachary Zelda
"""
GIVEN_NAMES = frozenset(GIVEN_NAMES_SOURCE.split())
# Title-Case words that end a heading or a label, never a surname: a name shape whose last word is one of these is prose.
NAME_STOPWORDS = frozenset("""
A Account Accounts Actual Add Admin All Also Amount An And Angeles Annual Any Approved Apr April Are At Aug
August Ave Avenue Balance Bank Basic Be Blvd Bonus Book Books Branch Build By California Camarillo Can Card Care
Cash Center Charge Charges Check City Client Clients Clinic Close Closing Co Code Codes Column Columns Commit
Company Contractor Contractors Corp Cost Costs County Create Credit Current Custom Customer Customers Daily Data
Date Day Debit Dec December Default Delete Demo Deploy Deposit Deposits Desktop Detail Details Did Dispute
Disputes Do Doctor Does Done Dr Draft Drive Due Each East Edit Else Employee Employees Entries Entry Error
Errors Every Example Expected Expense Expenses Failed Fake Feb February Fee Fees Field Fields File Files Filter
Final Find First For Free Friday From Get Gold Got Gross Group Gusto Had Has Have Health Hide Hormone Hour Hours
How Id Ids If In Inc Income Input Insurance Invoice Invoices Is It Item Items Jan January Journal Jul July Jun
June Just Key Keys LLC Lab Label Labels Labs Last Ledger Line Lines List Llc Los Loss Made Main Make Manager Map
Mar March Master May Medicaid Medical Medicare Member Members Membership Men Men's Mens Merge Might Mock Monday
Month Monthly Mr Mrs Ms Must Name Names Net New Next No None North Not Note Notes Nov November Number Numbers
Oct October Of Old On Online Only Open Opening Or Order Orders Output Owner Oxnard Page Pages Paid Patient
Patients Pay Payment Payments Payout Payouts Payroll Pending Peptide Peptides Plan Plans Plus Post Premium
Previous Price Prices Pro Profit Provider Put QuickBooks Quickbooks Rate Rates Rd Real Record Records Refund
Remove Renewal Report Reports Request Response Revenue Review Road Row Rows Rule Rules Run Salary Sample San
Santa Saturday Save Search Section See Semaglutide Sep Sept September Service Services Set Shall Should Show
Shows Silver Some Sort South Square St Staff Standard State Status Ste Step Steps Street Stripe Subscription
Subscriptions Subtotal Success Suite Summary Sunday Table Take Takes Tax Taxes Test Testosterone Tests Text That
The Then Therapy These This Those Thursday Tirzepatide To Total Transfer Trial Tuesday Type Types Update Use
Used User Users Uses Value Values Vendor Vendors Ventura View Visit Visits Warning Was Wednesday Week Weekly
Weight Were West What When Where Which Who Why Will With Without Women Year Yes
""".split())
PERSONA_WORD = re.compile(r"(?i)\b(?:roe|doe|poe|bloggs|mockley|stubbs|fakeman)\b")
NAME_WORD = re.compile(r"\b[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?\b")
NAME_GAP = re.compile(r"\s+(?:[A-Z]\.?\s+)?")                       # one space run, or a middle initial
CURRENCY = r"\$\s?\d[\d,]*(?:\.\d{2})?"
MONEY = re.compile(CURRENCY + r"|\b\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b|\b\d+\.\d{2}\b|(?<![\d/.:-])\d{4,}(?![\d/:-]|\.\d)"
                   r"|(?i:\b(?:paid|invoice[ds]?|payroll|salary|salaries|wages?|pay|payout|owes?|owed|bonus|refund(?:ed)?|reimburse\w*)\b)")
DATEISH = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|(?i:\b" + MONTH + r"\s+\d{1,2},?\s+\d{4})|(?<![$\d,.])\b(?:19|20)\d\d\b(?!\.\d)")   # a bare year, not $2000 or 1999.00
PLACE_PREFIX = re.compile(r"\b(?:San|Santa|Saint|St\.?|Los|Las|El|La|Del|Fort|Port|Mount|Mt\.?|Lake)\s+$")   # San Luis Obispo
NAME_NEAR = 60                                # chars either side of a First Last name searched for money context
LONE_NAME_MONEY = re.compile(r"\b([A-Z][a-z]+)\b(?!\s+[A-Z])[^\n$]{0,25}?" + CURRENCY + r"|" + CURRENCY + r"[^\n]{0,15}?\b(?:to|for|from)\s+([A-Z][a-z]+)\b")
PAY_WORD = r"(?:(?:gross|net|take[- ]home|base)\s+)?(?:pay|paycheck|wages?|salary|bonus|hourly\s+rate|comp(?:ensation)?)"
PRONOUN_PAY = re.compile(r"(?i:\b(?:his|her)\s+" + PAY_WORD + r"\b)[^\n]{0,50}?" + CURRENCY + r"|" + CURRENCY
                         + r"[^\n]{0,50}?(?i:\b(?:his|her)\s+" + PAY_WORD + r"\b)"
                         r"|(?i:\b(?:she|he)\s+(?:takes\s+home|earns|makes|is\s+paid|was\s+paid))[^\n]{0,30}?" + CURRENCY)
POSSESSIVE_PAY = re.compile(r"\b([A-Z][a-z]+)(?:'s|\u2019s|&apos;s)\s+(?i:" + PAY_WORD + r")\b[^\n]{0,50}?" + CURRENCY)
PARTIAL_ID = re.compile(r"(?i)(?:\blast[\s_-]*(?:4|four)(?:[\s_-]*digits?)?|\bending(?:[\s_-]+in)?|\bends[\s_-]+(?:in|with))"
                        r"[\s:=\"'`#(-]{0,6}(\d{4})(?![\d/-]|\.\d)"
                        r"|(?<![\w*\u2022])(?:(?:[*\u2022]{4,}|[xX]{4,})[\s-]?){1,3}(\d{4})(?!\d)|(?<![\w*\u2022])\u2022{3}-(\d{4})(?!\d)")
ALLOWED_LAST4 = frozenset({"4711", "0000", "4242", "1111"})   # the 4711 block, all zeros, the standard test-card endings
NAME_KEY = re.compile(r"""(?i)(?<![a-z])["']?(?:last_?name|lname|sur_?name|family_?name|patient_?name|full_?name|display_?name"""
                      r"""|legal_?name|name_?last)["']?\s*[:=]\s*["']([^"'\n]{2,60})["']""")
UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
CITY_ZIP = re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+){0,2},\s*[A-Z]{2},?\s+(\d{5})(?:-\d{4})?(?!\d)")
ZIP_KEY = re.compile(r"""(?i)(?<![a-z])["']?(?:zip|zip_?code|zip5|postal_?code|postcode)["']?\s*[:=]\s*["']?(\d{5})(?:-\d{4})?(?!\d)""")
ANY_EMAIL = re.compile(r"(?i)(?<![a-z0-9._%+\-])([a-z0-9._%+\-]{1,64})@([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b")
RESERVED_DOMAIN = re.compile(r"(?i)(?:^|\.)(?:example\.(?:com|org|net)|example|test|invalid|localhost)$")
NO_PERSON_LOCAL = re.compile(r"(?i)^(?:noreply|no-reply|donotreply|do-not-reply|git)$")
MOCKUP_PATH = re.compile(r"(?i)(^|/)mockups?(/|$)")
DOC_FILE = re.compile(r"(?i)(^docs/|\.(?:md|markdown|mdx|txt|rst|adoc|html?)$)")
OPT_IN_RULES = ("fixture_name", "fixture_dob", "fixture_partial_id", "doc_uuid", "fixture_address", "email_domain")
CONFIG = ".repo-standard.json"


def name_amount(text):
    """A First [M.] Last name near money or pay words, or a lone given name next to a dollar amount."""
    words = list(NAME_WORD.finditer(text))
    for i, w in enumerate(words):
        if w.group(0) not in GIVEN_NAMES:
            continue
        run = [w]
        for nxt in words[i + 1:i + 3]:                 # up to two more words, each separated by one gap
            if NAME_GAP.fullmatch(text, run[-1].end(), nxt.start()):
                run.append(nxt)
            else:
                break
        while len(run) > 1 and run[-1].group(0) in NAME_STOPWORDS:
            run.pop()
        if len(run) < 2 or PERSONA_WORD.search(run[-1].group(0)) or PLACE_PREFIX.search(text, max(0, run[0].start() - 12), run[0].start()):
            continue
        before = DATEISH.sub(" ", text[max(0, run[0].start() - NAME_NEAR):run[0].start()])
        after = DATEISH.sub(" ", text[run[-1].end():run[-1].end() + NAME_NEAR])
        if MONEY.search(before) or MONEY.search(after):
            return True
    return any((m.group(1) or m.group(2)) in GIVEN_NAMES for m in LONE_NAME_MONEY.finditer(text))


def pay_near_person(text):
    return bool(PRONOUN_PAY.search(text)) or any(m.group(1) not in NAME_STOPWORDS for m in POSSESSIVE_PAY.finditer(text))


def partial_id(text):
    return any((m.group(1) or m.group(2) or m.group(3)) not in ALLOWED_LAST4 for m in PARTIAL_ID.finditer(text))


def fixture_name(text):
    return any(not ("${" in v or "{{" in v or PERSONA_WORD.search(v)) and re.search(r"[A-Za-z]{2}", v)
               for v in (m.group(1) for m in NAME_KEY.finditer(text)))


def fixture_dob(text):
    return any(not synthetic("dob", m) for m in PATTERNS["dob"][0].finditer(text))


def doc_uuid(text):
    return any(len(set(m.group(0).replace("-", "").lower())) > 1 for m in UUID.finditer(text))


def fixture_address(text):
    zips = [m.group(1) for rx in (CITY_ZIP, ZIP_KEY) for m in rx.finditer(text)]
    return any(not (z.startswith("000") or z.startswith("4711") or z == "99999") for z in zips)


def email_domain(text):
    for m in ANY_EMAIL.finditer(text):
        local, domain = m.group(1), m.group(2).lower()
        if not (RESERVED_DOMAIN.search(domain) or NO_PERSON_LOCAL.match(local) or domain.endswith("noreply.github.com")
                or domain.rsplit(".", 1)[-1].isdigit()):
            return True
    return False


# name -> (check, scope); scope is "all", "not_fixture", "fixture" (fixture and mockup paths), "fixture_only" (fixture paths;
# mockups already get the default dob and partial_id checks), "doc", "fixture_or_doc".
PERSON_RULES = {
    "name_amount": (name_amount, "all"),
    "pay_near_person": (pay_near_person, "all"),
    "partial_id": (partial_id, "not_fixture"),
    "fixture_name": (fixture_name, "fixture"),
    "fixture_dob": (fixture_dob, "fixture_only"),
    "fixture_partial_id": (partial_id, "fixture_only"),
    "doc_uuid": (doc_uuid, "doc"),
    "fixture_address": (fixture_address, "fixture"),
    "email_domain": (email_domain, "fixture_or_doc"),
}


def load_opt_in():
    """guard_rules from .repo-standard.json at the repo root (git toplevel of the current directory); none outside a repo."""
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    except OSError as e:                          # fail CLOSED: without git the opt-in rules cannot be known
        print(f"identifier guard: cannot run git ({e.strerror}); refusing to pass")
        sys.exit(2)
    if r.returncode != 0:
        return frozenset()
    path = os.path.join(r.stdout.strip(), CONFIG)
    if not os.path.isfile(path):
        return frozenset()
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:            # fail CLOSED: a broken config never switches rules off silently
        print(f"identifier guard: {CONFIG} is not valid JSON ({e}); refusing to pass")
        sys.exit(2)
    rules = cfg.get("guard_rules", []) if isinstance(cfg, dict) else None
    if not isinstance(rules, list) or not all(isinstance(x, str) for x in rules):
        print(f"identifier guard: {CONFIG}: guard_rules must be a list of rule names; refusing to pass")
        sys.exit(2)
    unknown = sorted(set(rules) - set(OPT_IN_RULES))
    if unknown:
        print(f"identifier guard: {CONFIG}: unknown guard_rules {', '.join(unknown)} (known: {', '.join(OPT_IN_RULES)}); refusing to pass")
        sys.exit(2)
    return frozenset(rules)


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


def check_line(path, lineno, text, findings, opt_in=frozenset()):
    if LOCKFILE.search(path) or LINE_MARKER.search(text):
        return
    fixture = bool(FIXTURE_PATH.search(path))
    for name, rxs in PATTERNS.items():
        if fixture and name in ("ccm", "mrn", "dob"):
            continue   # test fixtures may carry record-number/DOB shapes; names, phones, e-mails and SSNs are still checked there
        if any(not synthetic(name, m) for rx in rxs for m in rx.finditer(text)):   # every match judged on its OWN text, not the line
            findings.append((path, lineno, name))
    fixture_like = fixture or bool(MOCKUP_PATH.search(path))
    doc = bool(DOC_FILE.search(path))
    in_scope = {"all": True, "not_fixture": not fixture, "fixture": fixture_like, "fixture_only": fixture, "doc": doc,
                "fixture_or_doc": fixture_like or doc}
    for name, (rule, scope) in PERSON_RULES.items():
        if (name in OPT_IN_RULES and name not in opt_in) or not in_scope[scope]:
            continue
        if rule(text):
            findings.append((path, lineno, name))


GIT_DIFF = ["git", "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--unified=0"]


def scan_diff(args, opt_in=frozenset()):
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
                check_line(path, lineno, line[1:], findings, opt_in)
    return findings


def scan_files(paths, opt_in=frozenset()):
    findings = []
    for p in paths:
        try:
            with open(p, "rb") as f:
                if b"\0" in f.read(8192):
                    continue                   # binary (PDF, font, image): not prose
            with open(p, encoding="utf-8", errors="replace") as f:
                for i, text in enumerate(f, 1):
                    if len(text) <= MAX_LINE:
                        check_line(p, i, text, findings, opt_in)
        except OSError as e:                   # fail CLOSED: an unreadable file is never "clean"
            print(f"identifier guard: cannot read {p}: {e.strerror}; refusing to pass")
            sys.exit(2)
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base"); ap.add_argument("--head", default="HEAD"); ap.add_argument("--staged", action="store_true")
    ap.add_argument("--files", nargs="*"); ap.add_argument("--three-dot", action="store_true")
    a = ap.parse_args()
    opt_in = load_opt_in()
    if a.files:
        findings = scan_files(a.files, opt_in)
    elif a.staged:
        findings = scan_diff(["--cached"], opt_in)
    else:
        if not a.base:
            sys.exit("need --base <rev> (or --staged / --files)")
        findings = scan_diff([f"{a.base}...{a.head}" if a.three_dot else f"{a.base}..{a.head}", "--"], opt_in)
    if findings:
        print("identifier guard: possible patient identifiers in added lines (file:line pattern). " + ALLOWED_SHAPES
              + " If this is a push to the default branch, the text is already in history: tell Steve before doing anything else.")
        for p, n, k in findings:
            print(f"  {p}:{n} {k}")
        sys.exit(1)
    print("identifier guard: clean")


if __name__ == "__main__":
    main()
