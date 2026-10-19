# ci-recipes

Shared CI recipes for ccmenshealth repositories. This repo holds nothing private: no secrets, no self-hosted runner,
no dependency on a private repo.

## identifier-guard

`identifier-guard/identifier-guard.py` fails a commit or pull request whose added lines look like patient identifiers
(MRN/FIN, CCM record numbers, DOB, SSN, US phone numbers, `LAST, FIRST` next to a number, personal e-mail) or person
data (a `First Last` name near money, pay tied to a person, last-4 fragments; more rules opt-in per repo, see
STANDARD.md, Person data). It prints
file, line and pattern only, never the matched text. Allowed test-data shapes, the only ones it exempts: ROE/DOE/POE
personas, 555-01xx or area-code-555 phones (and any impossible NANP number), the 4711 number block, CCM-471NN,
999-prefixed sentinel record numbers, the placeholder dates 1900-01-01 and 1970-01-01, example.com e-mail, and the words
synthetic / fixture / placeholder / sentinel on the line. Record-number and DOB shapes are also allowed under fixture paths.
Dependency lockfiles (`composer.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Cargo.lock`, `Package.resolved`,
exact basenames) are not scanned: machine-written, never our data.
Besides test data it exempts exactly two named business sets, exact numbers only: `PRACTICE_NUMBERS`, the practice's own
published phone and fax (letterhead, website, forms), and `REGULATOR_NUMBERS`, the regulator's published toll-free number
quoted in consent text. Never add a person's number to either. `LAST, FIRST` is not flagged when the first number after it
is any standalone 5-digit number (the CPT / test-code shape) or a LOINC code (`12345-6`), unless a 7+ digit number or a date
follows within 80 characters; a 6+ digit first number, a date, ZIP+4 and five digits after an upper-case state
abbreviation still are. A labelled `MRN`/`FIN` number of any length is caught by its own pattern.

Every ccmenshealth repo carries a byte-identical copy at `scripts/identifier-guard.py` (so the pre-commit hook in
`.githooks/pre-commit` works offline) and a thin workflow that calls the reusable workflow here:

```yaml
jobs:
  guard:
    uses: ccmenshealth/ci-recipes/.github/workflows/identifier-guard.yml@v1.5   # exact release tag; sync.sh writes the current one
```

The reusable workflow checks out the caller, fails if its `scripts/identifier-guard.py` differs from the canonical
copy (with the fix in the error), then runs the caller's own copy: three-dot diff against the PR base on
`pull_request`, the default branch's last 8 days on `schedule` / `workflow_dispatch`. Input `runs-on` (default
`ubuntu-latest`) lets a private caller use its own Linux runner; public callers never do.

### Changing the guard

1. Add a failing test row to `identifier-guard/tests/test_identifier_guard.py` (stdlib unittest; literals in the test
   file use only the allowed shapes, flagged values are assembled at run time), then fix the script.
2. `python3 -m unittest discover -s identifier-guard/tests` and open a PR here; CI runs the tests and scans this repo.
3. After the merge, cut the next release tag on the merge commit (`git tag v1.N <commit> && git push origin v1.N`,
   lightweight, never moved) and run `./sync.sh` from a clone at that commit: it pins every caller to the latest v1.N
   on origin (or `--tag`), refuses a tag whose guard is not this clone's, clones each repo, copies the script, hook and
   pinned caller, and opens one PR per repo with the repo's full-scan delta (file:line + pattern) in the body.
   `./sync.sh --only repo1,repo2` for a subset, `--dry-run` to look first. Until a repo merges its PR it keeps running
   its old pin, which still passes; nothing goes red between releases.

## repo-standard

[`STANDARD.md`](STANDARD.md) is the standard every repo follows (doc set, images and binary documents, secrets, test data, guard,
PR template, public-repo rule). `repo-standard/check.py` enforces the checkable part; a caller opts in with `with: standard: true`
and the reusable workflow runs it after the scan in the same job. `template/` is what a new repo starts from
(`./new-repo.sh <name> --private|--public [--dry-run]`); `./sync.sh --standard --only <repo>` adopts the standard in an
existing repo, one PR each. Tests: `python3 -m unittest discover -s repo-standard/tests`.

Tags: callers pin an exact release tag (`v1.N`); there is no moving tag, so no tag is ever force-pushed. Every merge
that callers should pick up gets the next `v1.N`, and `sync.sh` moves each repo to it in its own PR; a change to the
reusable workflow's inputs that existing callers cannot take as is starts `v2.0`. All tags are lightweight (no `-a`), so
`job.workflow_sha` in the reusable workflow is the commit.
The personal and organization copies of ci-recipes carry the same guard bytes under the same tag from v1.8 on; v1.7
(the composite action) exists only in the organization copy, so the personal copy goes from v1.6 to v1.8.

### Files

| Path | What |
|---|---|
| `identifier-guard/identifier-guard.py` | the canonical script |
| `identifier-guard/pre-commit` | the hook each repo installs at `.githooks/pre-commit` (`git config core.hooksPath .githooks` once per clone) |
| `identifier-guard/caller-workflow.yml` | the thin workflow each repo carries at `.github/workflows/identifier-guard.yml` |
| `identifier-guard/tests/` | unit tests |
| `.github/workflows/identifier-guard.yml` | the reusable workflow (`workflow_call`) |
| `.github/workflows/test.yml` | CI for this repo |
| `sync.sh` | rollout: one PR per repo (`--standard` adopts the repo standard) |
| `STANDARD.md` | the repo standard |
| `repo-standard/check.py`, `repo-standard/tests/` | the standard check and its tests |
| `template/` | the files a new repo starts from |
| `new-repo.sh` | create a repo from the template |
| `sweep.sh` | read-only audit of every repo against the standard (run from the owner's Mac) |
| `lib/apply.sh`, `lib/tests/` | file placement and tag pinning shared by `sync.sh` and `new-repo.sh`, and its tests |
| `org-move/move-repo.sh` | move one repo from the personal account to the organization as one "Initial import" commit, phase by phase (header lists the phases) |

### Cost

One GitHub-hosted Linux job per guard run in each repo, billed as 1 minute ($0.006); the reusable workflow's second
checkout adds a few seconds inside that minute, and the opt-in standard step about a second ($0). This repo's own
tests run a few times a month: under $0.10/month.
