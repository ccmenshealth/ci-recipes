# Repo standard

Every ccmenshealth repo, present and future, follows these rules. `repo-standard/check.py` enforces the ones a machine
can check; the rest are review rules. Each rule ends with why it exists.

## Documents

A repo holds only documentation that helps someone maintain the code. The fixed set:

| Path | What | Required |
|---|---|---|
| `README.md` | what it is, how to run it, how it deploys | yes |
| `CLAUDE.md` | rules for working in the repo (may be one line, `@AGENTS.md`, when the rules live in `AGENTS.md`) | yes |
| `AGENTS.md` | the same rules for other agents | no |
| `docs/architecture.md` | how the parts fit together | when there is more than one part |
| `docs/decisions.md` | dated decisions that still hold | when decisions exist |
| `docs/runbooks/` | generic procedures (deploy, rotate, restore) | when there are procedures |
| a spec | the product spec, where one exists | no |
| `docs/plans/` | plan documents, only while they carry decisions; no ledgers inside them | no |

- **No narrative files**: no histories of the work, hand-overs, ledgers, progress logs, to-do lists, audits, archives,
  status files, bookmarks, resume or startup prompts. Status and "what happened when" go to the memory system
  (`~/.claude/projects/<repo>/memory/`), which loads per repo and never leaves the Mac. *Why: narrative goes stale,
  duplicates git, and is the kind of text most likely to carry
  an identifier.*
- **No records of people or events**: no meeting notes, minutes, transcripts (including `.vtt` / `.srt` caption files),
  1-on-1 or stand-up notes, or audit dumps. *Why: they are about people, not code, and carry names, figures and ids
  that no scan can tell apart from real ones.*
- **Diagrams are text**: Mermaid or ASCII inside the markdown, never an image (no image lives under `docs/`).
  *Why: text diffs, reviews and scans like the rest of the doc.*
- **Records state the rule, never the event**: commit messages, PRs, docs and notes say what a thing now is, not what
  it was or what went wrong. *Why: a record of an event can imply what data was where; a rule cannot.*

## Images and binary documents

Only images and binary documents (PDF, Word, Excel, PowerPoint, OpenDocument) that code references or serves: logos,
app and web icons, blank forms the product serves, generated synthetic goldens, tiny synthetic fixtures. Never a
screenshot of a live screen or an export of live data; mockups are HTML files, not captures. *Why: the guard cannot
read an image or a binary document, so the only safe one is one nobody had to take from a live system to make.*

Default allowed paths (case-insensitive): `public/**`, `**/public/**`, `assets/**`, `**/assets/**`,
`fixtures/golden/**`, `fixtures/synthetic/**`, `**/*.ico`, `**/*.icns`, `**/*.appiconset/**`, `**/*.iconset/**`,
`**/*.xcassets/**`, `**/icons/**`, `**/icon/**`, `**/app/icon.*`, `**/app/apple-icon.*`. Any other asset directory (a
fixture folder, a logo folder, a folder of blank forms) is named in `asset_paths` in `.repo-standard.json` after
someone has looked at what is in it. Nothing of either kind under `docs/` is ever allowed, whatever the directory:
docs are prose.

## Secrets

Secrets, keys and trigger/callback URLs with signatures are never tracked; a served path never holds anything but site
files. A value that has been tracked is rotated, not just deleted. Documentation uses a placeholder (`REDACTED`,
`<key>`, `${VAR}`). *Why: anything tracked is copied to every clone and, on a served path, to every visitor.*

## Test data

There is no "synthetic PHI"; there is test data, and it uses only these shapes, the only ones the identifier guard
exempts. *Why: a value in patient shape that is not one of these cannot be told apart from a real one.*

- Persona surnames ROE, DOE, POE, BLOGGS, MOCKLEY, STUBBS, FAKEMAN.
- Phones 555-01xx and area code 555.
- The 4711 number block; record numbers CCM-471NN; the 999-prefixed sentinel.
- Placeholder dates 1900-01-01 and 1970-01-01.
- E-mail at example.com (example.org / example.net and the reserved `.test`, `.example`, `.invalid` names also pass).
- Last-4 fragments: 4711, 0000, or the standard test-card endings 4242 and 1111.
- ZIP codes 000xx, 4711x or 99999.
- Amounts are round and invented, never a real payment, invoice or pay figure; no figure is tied to a named person.

Generated fixtures (a script that writes the values) are preferred; hand-typed values live only under fixture paths.
*Why: a generator states the shape once and cannot copy a real value by accident.*

## Identifier guard

Every repo carries `scripts/identifier-guard.py` (byte-identical to `identifier-guard/identifier-guard.py` here),
`.githooks/pre-commit` (byte-identical to `identifier-guard/pre-commit`; `git config core.hooksPath .githooks` once per
clone) and `.github/workflows/identifier-guard.yml`, the thin caller of the reusable workflow. *Why: the hook stops an
identifier before it is committed, offline; CI stops it before it is merged; one canonical copy keeps them identical.*
Change the guard here, cut the next `v1.N` tag and run `./sync.sh`; never edit a repo's copy. The caller pins that exact
tag (no moving tag), so a repo changes only through its sync PR. Tags are lightweight, so the reusable workflow's
`job.workflow_sha` is the commit itself.

## Person data

A repo never holds business financials, pay, or customer, patient or staff names, and no figure is ever tied to a
person. The guard checks these shapes on added lines, by rule name (the name it prints):

| Rule | Default | Flags |
|---|---|---|
| `name_amount` | on | a `First Last` name (first word from the guard's given-name shape list, last word not a persona surname or a heading word) within 60 characters of money (`$` amount, `1,234.56`, a 4+ digit number that is not a date) or of paid / invoice / payroll / salary / wage / pay / payout / owe / bonus / refund / reimburse; or a lone given name right before a `$` amount, or `$amount to/for/from Name` |
| `pay_near_person` | on | `his/her [gross/net] pay/wages/salary/bonus` near a `$` amount, `she/he takes home/earns … $amount`, `Name's pay … $amount` |
| `partial_id` | on, outside fixture paths | `last 4` / `ending in` / `ends with` followed by four digits, or a masked `****4711` / `•••• 4711` / `XXXX-XXXX-4711` / `•••-4711`, other than the allowed last-4 values |
| `fixture_partial_id` | opt-in | the same in fixture paths |
| `fixture_name` | opt-in | in fixture and mockup paths, a `lastName` / `surname` / `familyName` / `patientName` / `fullName` / `displayName` / `legalName` value without a persona surname |
| `fixture_dob` | opt-in | in fixture paths (mockups already get the default DOB check), a labelled birth date other than the placeholders (fixture paths are otherwise exempt from the DOB check) |
| `fixture_address` | opt-in | in fixture and mockup paths, `City, ST 12345` or a `zip` / `postalCode` value outside the allowed ZIPs |
| `doc_uuid` | opt-in | a UUID in a doc file (`docs/`, `.md`, `.txt`, `.rst`, `.html`, …): operation and resource ids from live systems |
| `email_domain` | opt-in | in fixture, mockup and doc paths, any e-mail address outside the reserved domains (`example.com/.org/.net`, `.example`, `.test`, `.invalid`, `localhost`); `noreply@` / `git@`, `*.noreply.github.com` and version specifiers (`pkg@1.2.3`) pass |

A repo switches opt-in rules on with `guard_rules` in `.repo-standard.json` at the repo root; the hook and CI read the
same key, from the working tree (so a PR that drops a rule is judged without it: review the config change). The
given-name list is a shape list (common given names that are not also ordinary English words), not a list of people,
and stays ASCII-sorted. *Why: the shapes an independent read found in repos that passed every other scan.*

The guard does not see a surname inside free text (a memo string), a name on a line without an amount, a single
surname next to an amount, figures in a comment that names nobody, a name in `Last, First` order or in capitals next to
an amount, or names with an inner capital, an apostrophe or an accent (`McX`, `O'X`, letters outside A-Z). That is why the independent certification read
stays the gate before any repo moves, whatever the guard says.

## Pull requests

`.github/PULL_REQUEST_TEMPLATE.md` exists and has a line that starts `CI cost` (template:
`template/.github/PULL_REQUEST_TEMPLATE.md`). Every PR that adds or changes a workflow trigger, job, schedule, matrix
or runner fills it in with the added GitHub-hosted minutes and dollars per month. *Why: CI minutes cost money; a
line in every PR puts the cost where the change is made.*

## Public repos

A public repo never touches a system that may hold patient data: no secret (other than `GITHUB_TOKEN`), no
self-hosted runner, no runner but `ubuntu-*` (no new GitHub-hosted macOS or Windows jobs), no dependency on a private
repo. Set `"public": true` in `.repo-standard.json`. *Why: anyone can
open a pull request against a public repo, and its workflows run with whatever the repo can reach.*

## `.repo-standard.json`

```json
{
  "version": 1,
  "public": false,
  "asset_paths": ["public/**", "assets/**", "forms/blank/**"],
  "allow": ["docs/plans/widget-ledger.md"],
  "guard_rules": ["fixture_dob", "email_domain"]
}
```

- `public`: applies the public-repo rule.
- `asset_paths`: replaces the default asset allowlist when present (the template lists the default). An entry that
  would allow an image or binary document at the top level or under `docs/` is refused. (`image_paths`, its old name,
  is an error that says so.)
- `allow`: exact tracked paths exempt from the narrative-name, narrative-directory and person-record-path rules, for a
  file whose name only looks narrative (a feature called "ledger", a plan for a transcript feature). An entry for a
  file that no longer exists is an error. `allow` never exempts a secret.
- `guard_rules`: the guard's opt-in rules this repo runs (`fixture_name`, `fixture_dob`, `fixture_partial_id`,
  `doc_uuid`, `fixture_address`, `email_domain`; see Person data). An unknown name is an error in both the check and
  the guard.

Unknown keys and wrong types are errors, so a typo never switches a rule off silently.

## The check

`python3 repo-standard/check.py --root <repo>` (stdlib only) looks at tracked files and prints one tab-separated line
per violation: rule, path, fix. Paths only, never file content. Exit 0 clean, 1 violations, 2 error.

| Rule | Fails when |
|---|---|
| `narrative-name` | a document file (`.md`, `.txt`, `.rst`, `.html`, `.pdf`, `.docx`, …, or no extension) is named like narrative: contains hand-off/handoff, ledger, current-state, resume-prompt or startup-prompt; a history of the work (`session-history`, `change-history`, `HISTORY.md`); or the whole name is progress, status, todo(s), bookmark(s) |
| `narrative-dir` | any file under `docs/` sits in a directory named progress, todo(s), audit(s), archive(d), history/histories, handoff(s), ledger(s), sessions or status; or a top-level directory is named archive(d), history/histories or handoff(s) |
| `person-record-path` | a document file sits in a directory named meeting(s), meeting-notes / meeting_notes, minutes or transcripts, or its name has the whole word meeting(s), minutes, transcript(s), audit-dump, 1-on-1 / one-on-one, standup, retro or retrospective; or any `.vtt` / `.srt` file |
| `binary-path` | an image (`png jpg jpeg gif webp bmp tif tiff heic heif avif svg ico icns`) or binary document (`pdf doc docx xls xlsx ppt pptx odt ods odp`) is under `docs/` or outside the allowed paths |
| `secret-shape` | a tracked text file (no NUL byte, at most 2 MB) has a signed URL (`sig=`, also as `\u0026sig=` or `%26sig%3D`), SAS token, storage `AccountKey=`, `SharedAccessSignature`, private-key or PGP private-key block with a body, GitHub, AWS or Slack token, or a long bearer token; reported as path:line and the shape's name, never the text. Placeholders pass; Azure client secrets have no shape and are not detected |
| `required-file` | `README.md` or `CLAUDE.md` is missing |
| `pr-template` | `.github/PULL_REQUEST_TEMPLATE.md` is missing or has no `CI cost` line |
| `guard` | `scripts/identifier-guard.py` is missing or differs from the canonical copy |
| `hook` | `.githooks/pre-commit` is missing or differs from the canonical hook |
| `public-repo` | (public repos) a workflow or local action names a secret other than `GITHUB_TOKEN` (including `secrets: inherit`), runs on anything but a literal `ubuntu-*` label, or uses or checks out a `ccmenshealth/` repo other than ci-recipes |

Names only count for document files: a source file called `history.ts` or `handoff.py` is code, not narrative.
"History" alone is a feature or clinical word (payment history, medical history) and passes. A document about a
feature that happens to be called a ledger or a hand-off goes in `allow`.

Not machine-checked (review rules): which documents sit under `docs/` beyond the narrative names; what an image or
binary document in an allowed path shows; secrets without a recognisable shape; wording of records (rule, not event); a repo that has no caller workflow at all (see New repos).

## In CI

The reusable workflow runs the check after the identifier scan, in the same job, when the caller passes
`standard: true`. The caller change, one block under `uses:`:

```yaml
jobs:
  guard:
    if: github.event_name != 'pull_request' || github.event.pull_request.draft == false
    uses: ccmenshealth/ci-recipes/.github/workflows/identifier-guard.yml@v1.5   # exact release tag; sync.sh writes the current one
    with:
      standard: true
```

The scan always runs first, so a standard failure never hides its result. A public caller gets the public-repo rule
from its visibility as well as from the config. Cost: about a second inside the existing one-minute job, $0.

## New repos

`./new-repo.sh <name> --private|--public` builds the repo from `template/`, adds the guard, checks the tree, creates the
GitHub repo, and sets delete-branch-on-merge, no wiki, Actions with a read-only token (a public repo allows only
GitHub-owned actions and this repo's reusable workflow). It prints what the owner still does: secrets, CodeRabbit,
the hook, memory. `--dry-run` builds and checks the tree and prints the GitHub calls without making them.

A repo that skipped the template has no caller, so nothing in it runs the check. `./sweep.sh` (read-only) lists every
non-archived repo of the owner, reports whether it has the caller with `standard: true`, and with `--check` runs
`check.py` on a fresh clone of each. This repo's CI token cannot read other repos, so the sweep runs
from the owner's Mac (`gh` logged in as the owner); the owner's weekly scheduled task is its intended caller.

## Existing repos

`./sync.sh --standard --only <repo>` opens one PR per repo that adds the PR template (or appends the CI-cost section to
an existing one), `.repo-standard.json`, a one-line `CLAUDE.md` if missing, and the caller with `standard: true`, and
puts the check's findings in the PR body. Fix the findings on that branch (rename, move, delete, or a config entry)
before merging. After that, a plain `./sync.sh` keeps the repo on the standard caller.

ci-recipes itself is the source of the standard, not a consumer: the canonical guard lives in `identifier-guard/`, so
it is not checked against itself.
